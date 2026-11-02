"""Proposals (ADR-020): sync changes become reviewable re-plans; apply, reject and undo; no duplicates."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from peace.api import app
from peace.assistant import cmd_review, match_rules
from peace.models import AuditEvent, Notification, Proposal, StudySession, Task
from peace.notifications import generate
from peace.routes import get_now
from test_sync import NOW, ir_snap, m_snap, sync

LISBON = ZoneInfo("Europe/Lisbon")


@pytest.fixture
def at_now():
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def later_session(session, task, days=5):
    row = StudySession(task_id=task.id, course_id=task.course_id, starts_at=NOW + timedelta(days=days),
                       ends_at=NOW + timedelta(days=days, hours=1), reason="plano antigo")
    session.add(row)
    session.commit()
    return row


def moved_earlier(session):
    """Meta3 due in 10 days, studied on day 5; Moodle then moves the deadline to 2 days from now."""
    sync("moodle", m_snap(due=NOW + timedelta(days=10)))
    task = session.query(Task).one()
    old = later_session(session, task)
    sync("moodle", m_snap(due=NOW + timedelta(days=2)))
    session.expire_all()
    return task, old


def test_first_import_proposes_nothing(session):
    sync("moodle", m_snap(due=NOW + timedelta(days=3)))
    assert session.query(Proposal).count() == 0


def test_deadline_moved_earlier_becomes_one_pending_replan(session, client, at_now):
    task, old = moved_earlier(session)
    proposal = session.query(Proposal).one()
    assert (proposal.status, proposal.kind, proposal.task_id) == ("pending", "replan", task.id)
    assert proposal.reason.startswith("Prazo antecipado 8 dias")
    assert proposal.evidence[0]["source"] == "moodle" and proposal.evidence[0]["rule"] == "deadline_earlier"
    # The preview is the real action, rolled back: nothing changed yet.
    shown = client.get("/api/v1/proposals").json()[0]["preview"]
    assert [r["id"] for r in shown["removed"]] == [old.id]
    assert shown["created"] and all(datetime.fromisoformat(c["ends_at"]) <= task.due_at for c in shown["created"])
    assert session.query(StudySession).count() == 1

    sync("moodle", m_snap(due=NOW + timedelta(days=2)))  # the same data again
    assert session.query(Proposal).count() == 1


def test_apply_replaces_only_that_tasks_sessions_and_undo_restores_them(session, client, at_now):
    task, old = moved_earlier(session)
    old_id, old_times = old.id, (old.starts_at, old.ends_at)
    other = StudySession(course_id=task.course_id, starts_at=NOW + timedelta(days=6), ends_at=NOW + timedelta(days=6, hours=1))
    session.add(other)
    session.commit()
    pid = session.query(Proposal).one().id

    applied = client.post(f"/api/v1/proposals/{pid}/apply").json()
    assert applied["status"] == "applied" and [r["id"] for r in applied["result"]["removed"]] == [old_id]
    session.expire_all()
    rows = session.query(StudySession).filter_by(task_id=task.id).all()
    assert rows and all(r.ends_at <= task.due_at for r in rows)
    assert session.get(StudySession, other.id) is not None  # someone else's session is untouched
    assert session.query(AuditEvent).filter_by(action="proposal.apply").count() == 1

    assert client.post(f"/api/v1/proposals/{pid}/undo").json()["status"] == "undone"
    session.expire_all()
    back = session.query(StudySession).filter_by(task_id=task.id).one()
    assert (back.starts_at, back.ends_at, back.reason) == (*old_times, "plano antigo")


def test_undo_is_refused_once_a_created_session_was_done(session, client, at_now):
    moved_earlier(session)
    pid = session.query(Proposal).one().id
    created = client.post(f"/api/v1/proposals/{pid}/apply").json()["result"]["created"]
    assert client.patch(f"/api/v1/sessions/{created[0]['id']}", json={"status": "done"}).status_code == 200
    r = client.post(f"/api/v1/proposals/{pid}/undo")
    assert r.status_code == 409 and "à mão" in r.json()["detail"]
    session.expire_all()
    assert session.get(Proposal, pid).status == "applied"


def test_reject_changes_nothing_and_cannot_be_decided_twice(session, client, at_now):
    _, old = moved_earlier(session)
    pid = session.query(Proposal).one().id
    assert client.post(f"/api/v1/proposals/{pid}/reject").json()["status"] == "rejected"
    assert client.post(f"/api/v1/proposals/{pid}/apply").status_code == 409
    session.expire_all()
    assert session.get(StudySession, old.id) is not None
    assert [p["status"] for p in client.get("/api/v1/proposals?status=history").json()] == ["rejected"]


def test_closed_task_makes_the_proposal_stale(session, client, at_now):
    task, _ = moved_earlier(session)
    task.status = "COMPLETED"
    session.commit()
    pid = session.query(Proposal).one().id
    assert client.get("/api/v1/proposals").json()[0]["preview"]["error"] == "A tarefa já está fechada."
    r = client.post(f"/api/v1/proposals/{pid}/apply")
    assert (r.status_code, r.json()["detail"]) == (409, "A tarefa já está fechada.")


def test_a_newer_change_supersedes_the_pending_one(session):
    moved_earlier(session)
    sync("moodle", m_snap(due=NOW + timedelta(days=1)))
    session.expire_all()
    assert sorted(p.status for p in session.query(Proposal)) == ["pending", "superseded"]


def test_switched_off_rule_proposes_nothing(session, client):
    rules = client.get("/api/v1/preferences/review").json()
    assert rules["deadline_earlier"] is True and rules["deadline_later"] is False
    client.put("/api/v1/preferences/review", json={**rules, "deadline_earlier": False})
    moved_earlier(session)
    assert session.query(Proposal).count() == 0


def test_nothing_to_change_proposes_nothing(session, client):
    client.put("/api/v1/preferences/review", json={**client.get("/api/v1/preferences/review").json(), "deadline_later": True})
    sync("moodle", m_snap(due=NOW + timedelta(days=3)))
    task = session.query(Task).one()
    task.estimated_minutes = 60
    later_session(session, task, days=1)  # the whole hour of work is already planned and still fits
    sync("moodle", m_snap(due=NOW + timedelta(days=4)))
    assert session.query(Proposal).count() == 0


def test_new_assessment_and_new_assignment_are_proposed(session):
    sync("inforestudante", ir_snap())
    sync("moodle", m_snap(due=NOW + timedelta(days=60)))  # first Moodle import: silent
    snap = ir_snap()
    snap.events.append({**snap.events[1], "external_id": "avaliacoes:9", "title": "AM1 (Frequência 2)",
                        "starts_at": NOW + timedelta(days=5), "ends_at": NOW + timedelta(days=5, hours=2)})
    sync("inforestudante", snap)
    moodle = m_snap(due=NOW + timedelta(days=60))
    moodle.tasks.append({**moodle.tasks[0], "external_id": "assign:2", "title": "Meta4", "due_at": NOW + timedelta(days=4)})
    sync("moodle", moodle)
    rules = sorted(p.evidence[0]["rule"] for p in session.query(Proposal))
    assert rules == ["assessment_new", "assignment_new"]


def test_replan_across_the_dst_change_stays_in_the_local_study_window(session, client):
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)  # Saturday; clocks go back on Sunday 25
    app.dependency_overrides[get_now] = lambda: now
    try:
        sync("moodle", m_snap(due=now + timedelta(days=10)), now=now)
        later_session(session, session.query(Task).one())
        sync("moodle", m_snap(due=datetime(2026, 10, 26, 20, 0, tzinfo=UTC)), now=now)
        pid = session.query(Proposal).one().id
        created = client.post(f"/api/v1/proposals/{pid}/apply").json()["result"]["created"]
    finally:
        app.dependency_overrides.pop(get_now, None)
    assert created
    for c in created:
        start, end = (datetime.fromisoformat(c[k]).astimezone(LISBON) for k in ("starts_at", "ends_at"))
        assert start.date() >= date(2026, 10, 24) and start.hour >= 9 and (end.hour, end.minute) <= (21, 0)


def test_assistant_and_notification_point_to_the_review(session):
    assert match_rules("o que há para rever?", NOW.date())[0] == "review"
    assert match_rules("tenho sugestões?", NOW.date())[0] == "review"
    assert cmd_review(session, NOW).text == "Não há nada para rever."
    moved_earlier(session)
    reply = cmd_review(session, NOW)
    assert reply.text.startswith("Tens 1 sugestão para rever") and reply.to == "/rever"
    generate(session, NOW, LISBON)
    note = session.query(Notification).filter_by(kind="changes").one()
    assert note.url == "/rever" and "1 sugestão para rever" in note.body


def test_asking_what_to_review_never_waits_for_the_model(session, monkeypatch):
    from peace import assistant

    monkeypatch.setattr(assistant, "llm_reachable", lambda: (_ for _ in ()).throw(AssertionError("model asked")))
    assert assistant._decide(session, "o que há para rever?", NOW.date(), NOW)[:1] == ("review",)

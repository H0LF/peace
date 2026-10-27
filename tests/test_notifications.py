from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from peace.api import app
from peace.models import Change, Event, Notification, Preference, StudySession, SyncRun, Task
from peace.notifications import generate
from peace.routes import get_now

LX = ZoneInfo("Europe/Lisbon")
NOW = datetime(2026, 10, 26, 13, 0, tzinfo=UTC)  # Monday 13:00 in Lisbon


def run(session, at=NOW):
    made = generate(session, at, LX)
    session.commit()
    return made


def keys(session):
    return sorted(n.key for n in session.query(Notification))


def test_deadline_reminders_24h_then_2h_are_idempotent_and_expire(session):
    task = Task(title="Relatório", due_at=NOW + timedelta(hours=20))
    session.add(task)
    session.commit()
    assert run(session) == 2  # the reminder and the daily briefing
    assert run(session) == 0
    assert [k for k in keys(session) if k.startswith("task")] == [f"task:{task.id}:24h"]
    run(session, NOW + timedelta(hours=19))
    urgent = session.query(Notification).filter_by(key=f"task:{task.id}:2h").one()
    assert urgent.urgent and urgent.actions == ["open", "done", "snooze"]
    assert urgent.body == "Relatório · hoje às 09:00"  # generated on Tuesday 08:00
    assert urgent.expires_at == task.due_at


def test_completed_tasks_get_no_reminders(session):
    session.add(Task(title="Feito", status="COMPLETED", due_at=NOW + timedelta(hours=3)))
    session.commit()
    run(session)
    assert not [k for k in keys(session) if k.startswith("task")]


def test_exam_and_session_start(session):
    exam = Event(title="BD (Teste 1)", kind="exam", starts_at=NOW + timedelta(hours=27), ends_at=NOW + timedelta(hours=29))
    soon = StudySession(starts_at=NOW + timedelta(minutes=3), ends_at=NOW + timedelta(minutes=53), reason="entrega esta semana")
    later = StudySession(starts_at=NOW + timedelta(minutes=30), ends_at=NOW + timedelta(minutes=80))
    session.add_all([exam, soon, later])
    session.commit()
    run(session)
    assert f"exam:{exam.id}:24h" not in keys(session)  # 27 h away
    assert f"session:{soon.id}:start" in keys(session) and f"session:{later.id}:start" not in keys(session)
    run(session, NOW + timedelta(hours=4))
    assert f"exam:{exam.id}:24h" in keys(session)


def test_changes_are_grouped_and_only_new_ones_notified(session):
    session.add_all([Change(source="moodle", entity="task", kind="created", summary=f"Novo trabalho: T{i}") for i in range(5)])
    session.commit()
    run(session)
    (first,) = session.query(Notification).filter_by(kind="changes").all()
    assert first.body == "Novo trabalho: T0\nNovo trabalho: T1\nNovo trabalho: T2\n+2 outras"
    run(session)
    session.add(Change(source="inforestudante", entity="event", kind="updated", summary="Data alterada: BD (Teste 1)"))
    session.commit()
    run(session)
    latest = session.query(Notification).filter_by(kind="changes").order_by(Notification.id.desc()).first()
    assert (latest.title, latest.body) == ("Novidade nos sites", "Data alterada: BD (Teste 1)")


def test_urgent_academic_change_bypasses_secondary_budget_and_plan_title_is_truthful(client, session, at):
    at(NOW)
    exam = Event(title="Teste", kind="exam", starts_at=NOW + timedelta(days=1), ends_at=NOW + timedelta(days=1, hours=2))
    session.add(exam)
    session.flush()
    session.add(Change(source="inforestudante", entity="event", entity_id=exam.id, kind="updated", summary="Data alterada: Teste"))
    session.add(Notification(key="changes:0", kind="changes", title="Already sent", delivered_at=NOW - timedelta(minutes=1)))
    session.commit()
    run(session)
    notice = session.query(Notification).filter_by(kind="changes", delivered_at=None).one()
    assert notice.urgent
    notice.deliver_after = NOW
    session.commit()
    assert notice.id in [n["id"] for n in client.get("/api/v1/notifications/pending").json()]
    session.add(Change(source="planner", entity="plan", kind="created", summary="Plano de estudo: 2 novas sessões."))
    session.commit()
    run(session)
    latest = session.query(Notification).filter_by(kind="changes").order_by(Notification.id.desc()).first()
    assert latest.title == "Plano de estudo atualizado"


def test_a_malformed_changes_key_cannot_stop_generation(session):
    session.add(Notification(key="recent", kind="changes", title="Malformada"))
    session.add(Change(source="moodle", entity="task", kind="created", summary="Novo trabalho: T"))
    session.commit()
    run(session)
    assert session.query(Notification).filter_by(key="changes:1").one().body == "Novo trabalho: T"


def test_missed_sessions_after_the_study_day(session):
    session.add(StudySession(starts_at=NOW - timedelta(hours=3), ends_at=NOW - timedelta(hours=2)))
    session.commit()
    run(session)
    assert not [k for k in keys(session) if k.startswith("missed")]
    run(session, datetime(2026, 10, 26, 21, 5, tzinfo=UTC))
    missed = session.query(Notification).filter_by(kind="missed").one()
    assert missed.body == "1 sessão ficou por fazer hoje. Queres replanear amanhã?"
    assert missed.actions == ["replan", "open"]


def test_integration_problems_once_per_day(session):
    session.add(SyncRun(source="moodle", trigger="scheduled", status="auth_failed", finished_at=NOW))
    session.commit()
    run(session)
    run(session)
    assert [k for k in keys(session) if k.startswith("sync")] == ["sync:moodle:2026-10-26"]


def test_daily_briefing_text(session):
    session.add_all([
        Event(title="AM1 (P4)", kind="class", starts_at=NOW + timedelta(hours=2), ends_at=NOW + timedelta(hours=3)),
        Task(title="Atrasada", due_at=NOW - timedelta(hours=1), estimated_minutes=60),
        Task(title="Lab 04", due_at=NOW + timedelta(days=2), estimated_minutes=60),
    ])
    session.commit()
    run(session, datetime(2026, 10, 26, 7, 30, tzinfo=UTC))
    assert "briefing:2026-10-26" not in keys(session)  # before 08:00
    run(session)
    brief = session.query(Notification).filter_by(kind="briefing").one()
    assert brief.body == (
        "Hoje tens 1 aula. Tens 1 tarefa em atraso. Próximo prazo: Lab 04, quarta às 13:00. "
        "Recomendado: 50 minutos em «Atrasada» (em atraso)."
    )


def test_disabled_notifications_generate_nothing(session):
    session.add(Preference(key="notifications", value={"enabled": False}))
    session.add(Task(title="X", due_at=NOW + timedelta(hours=1)))
    session.commit()
    assert run(session) == 0


def test_disabling_preferences_holds_already_queued_notifications(client, session, at):
    at(NOW)
    session.add_all([Preference(key="notifications", value={"enabled": False}),
                     Notification(key="queued", kind="deadline", title="Queued", deliver_after=NOW)])
    session.commit()
    assert client.get("/api/v1/notifications/pending").json() == []


def test_completed_rescheduled_and_disabled_category_reminders_are_not_delivered(client, session, at):
    at(NOW)
    task = Task(title="Prazo", due_at=NOW + timedelta(hours=1))
    study = StudySession(starts_at=NOW + timedelta(minutes=1), ends_at=NOW + timedelta(minutes=51))
    exam = Event(title="Teste", kind="exam", starts_at=NOW + timedelta(hours=2), ends_at=NOW + timedelta(hours=4))
    session.add_all([task, study, exam])
    session.commit()
    run(session)
    session.query(Notification).update({"deliver_after": NOW})
    task.status, study.status = "COMPLETED", "skipped"
    exam.starts_at += timedelta(days=7)
    exam.ends_at += timedelta(days=7)
    session.commit()
    assert [n["kind"] for n in client.get("/api/v1/notifications/pending").json()] == ["briefing"]
    client.put("/api/v1/preferences/notifications", json={"deadlines": False})
    assert not [n for n in client.get("/api/v1/notifications/pending").json() if n["kind"] in ("deadline", "exam")]


def test_explicit_native_test_uses_queue_even_during_quiet_hours_and_reuses_pending_test(client, session, at):
    at(NOW.replace(hour=23, minute=30))
    client.put("/api/v1/preferences/notifications", json={"enabled": False})
    first = client.post("/api/v1/notifications/test")
    assert first.status_code == 201
    second = client.post("/api/v1/notifications/test")
    assert second.json()["id"] == first.json()["id"]
    assert [n["kind"] for n in client.get("/api/v1/notifications/pending").json()] == ["test"]
    diagnostics = client.get("/api/v1/notifications/diagnostics").json()
    assert diagnostics["desktop_connected"] is False
    assert diagnostics["visual_confirmation"] == "pending_user"
    client.post("/api/v1/desktop/notifications/status", json={"notification_id": first.json()["id"], "native_id": 41, "result": "accepted"})
    assert client.get("/api/v1/notifications/diagnostics").json()["delivery"]["result"] == "accepted"


def test_deadline_changes_rearm_existing_reminder_without_duplicates(session):
    task = Task(title="Prazo", due_at=NOW + timedelta(hours=10))
    session.add(task)
    session.commit()
    run(session)
    note = session.query(Notification).filter_by(key=f"task:{task.id}:24h").one()
    note.delivered_at = NOW
    old_snooze = NOW + timedelta(days=2)
    note.deliver_after = old_snooze  # an old snooze must not delay the changed deadline
    task.due_at = NOW + timedelta(hours=15)
    session.commit()
    run(session)
    session.refresh(note)
    assert note.delivered_at is None and note.expires_at == task.due_at
    assert note.deliver_after != old_snooze
    assert run(session) == 0


def test_pending_briefing_text_refreshes_but_a_delivered_briefing_stays_delivered(session):
    run(session)
    note = session.query(Notification).filter_by(kind="briefing").one()
    old = note.body
    task = Task(title="Entrega urgente", due_at=NOW + timedelta(minutes=30))
    session.add(task)
    session.commit()
    run(session)
    session.refresh(note)
    assert note.body != old and "Entrega urgente" in note.body
    note.delivered_at = NOW
    session.commit()
    delivered_body = note.body
    task.status = "COMPLETED"
    session.commit()
    run(session)
    session.refresh(note)
    assert note.delivered_at == NOW and note.body == delivered_body


def test_exam_close_warning_is_urgent_and_idempotent(session):
    session.add(Event(title="Teste", kind="exam", starts_at=NOW + timedelta(minutes=90), ends_at=NOW + timedelta(hours=3)))
    session.commit()
    run(session)
    note = session.query(Notification).filter_by(kind="exam").one()
    assert note.urgent and "2 horas" in note.title
    assert run(session) == 0


@pytest.fixture
def at():
    def set_now(when):
        app.dependency_overrides[get_now] = lambda: when

    yield set_now
    app.dependency_overrides.pop(get_now, None)


def test_pending_respects_quiet_hours_expiry_delivery_and_snooze(client, session, at):
    session.add_all([
        Notification(key="a", kind="deadline", title="A", deliver_after=NOW - timedelta(minutes=1)),
        Notification(key="old", kind="deadline", title="Old", deliver_after=NOW - timedelta(hours=5), expires_at=NOW - timedelta(hours=1)),
    ])
    session.commit()
    at(datetime(2026, 10, 26, 23, 30, tzinfo=UTC))
    assert client.get("/api/v1/notifications/pending").json() == []  # quiet hours (23:00-08:00)
    at(NOW)
    pending = client.get("/api/v1/notifications/pending").json()
    assert [n["title"] for n in pending] == ["A"]
    nid = pending[0]["id"]
    assert client.post(f"/api/v1/notifications/{nid}/delivered").status_code == 204
    assert client.get("/api/v1/notifications/pending").json() == []
    client.post(f"/api/v1/notifications/{nid}/snooze", json={"minutes": 30})
    at(NOW + timedelta(minutes=29))
    assert client.get("/api/v1/notifications/pending").json() == []
    at(NOW + timedelta(minutes=31))
    assert [n["id"] for n in client.get("/api/v1/notifications/pending").json()] == [nid]
    assert client.put("/api/v1/preferences/notifications", json={"quiet_start": "22:00", "quiet_end": "07:00"}).status_code == 200


def test_low_priority_notifications_are_rate_limited(client, session):
    from peace.models import Notification
    now = datetime(2026, 10, 26, 15, 0, tzinfo=UTC)
    app.dependency_overrides[get_now] = lambda: now
    try:
        session.add_all([Notification(key=f"c{i}", kind="changes", title=f"Novidade {i}", deliver_after=now - timedelta(minutes=5)) for i in range(3)]
                        + [Notification(key="d", kind="deadline", title="Prazo", deliver_after=now - timedelta(minutes=5))])
        session.commit()
        titles = lambda: [n["title"] for n in client.get("/api/v1/notifications/pending").json()]  # noqa: E731
        assert titles() == ["Prazo", "Novidade 0"]  # time-sensitive reminders precede the low-priority batch
        first = session.query(Notification).filter_by(key="c0").one()
        first.delivered_at = now - timedelta(minutes=3)
        session.commit()
        assert titles() == ["Prazo"]  # 10 min gap since the last one
        app.dependency_overrides[get_now] = lambda: now + timedelta(minutes=11)
        assert titles() == ["Prazo", "Novidade 1"]
        for i, key in enumerate(["c1", "c2"]):  # budget of the hour spent: 4 delivered in the last hour
            session.query(Notification).filter_by(key=key).one().delivered_at = now - timedelta(minutes=40 + i)
        session.add(Notification(key="c3", kind="missed", title="Ficou por fazer", deliver_after=now, delivered_at=now - timedelta(minutes=45)))
        session.add(Notification(key="c4", kind="integration", title="Falha", deliver_after=now))
        session.commit()
        assert titles() == ["Prazo"]
        client.put("/api/v1/preferences/notifications", json={**client.get("/api/v1/preferences/notifications").json(), "max_per_hour": 10})
        assert titles() == ["Prazo", "Falha"]
    finally:
        app.dependency_overrides.pop(get_now, None)


def test_a_backlog_of_low_priority_notifications_cannot_hide_a_reminder(client, session):
    from peace.models import Notification
    now = datetime(2026, 10, 26, 15, 0, tzinfo=UTC)
    app.dependency_overrides[get_now] = lambda: now
    try:
        session.add(Notification(key="sent", kind="changes", title="Já mostrada", deliver_after=now, delivered_at=now - timedelta(minutes=2)))
        session.add_all([Notification(key=f"c{i}", kind="changes", title=f"Novidade {i}", deliver_after=now - timedelta(hours=1)) for i in range(25)])
        session.add(Notification(key="exam", kind="exam", title="Avaliação amanhã", deliver_after=now - timedelta(minutes=1)))
        session.commit()
        assert [n["title"] for n in client.get("/api/v1/notifications/pending").json()] == ["Avaliação amanhã"]
    finally:
        app.dependency_overrides.pop(get_now, None)

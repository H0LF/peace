"""Sync engine and scheduler, with fake fetchers (no network). Real-site runs are recorded in TEST_RESULTS."""

import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from peace.models import Change, Course, Event, SyncRun, Task
from peace.sources import AuthError
from peace.sources.inforestudante import LISBON
from peace.sources.inforestudante import Snapshot as IRSnap
from peace.sources.moodle import Snapshot as MSnap
from peace.sync import AlreadyRunning, run_sync, source_lock
from peace.worker import is_due, last_slot, next_slot

NOW = datetime(2026, 10, 26, 13, 0, tzinfo=UTC)
WINDOW = (datetime(2026, 10, 26, tzinfo=LISBON), datetime(2027, 2, 15, tzinfo=LISBON))


def ir_snap(events=None, groups=("P4",)):
    events = events if events is not None else [
        {"external_id": "aulas:1", "kind": "class", "title": "AM1 (P4)", "starts_at": NOW + timedelta(days=1),
         "ends_at": NOW + timedelta(days=1, hours=1), "location": "S1.01", "course_code": "60000001"},
        {"external_id": "avaliacoes:2", "kind": "exam", "title": "AM1 (Frequência 1)", "starts_at": NOW + timedelta(days=16),
         "ends_at": NOW + timedelta(days=16, hours=2), "location": None, "course_code": "60000001"},
    ]
    courses = [{"code": "60000001", "name": "Análise Matemática I", "groups": list(groups)},
               {"code": "61000001", "name": "Base de Dados", "groups": []}]
    return IRSnap(courses=courses, events=events, window_start=WINDOW[0], window_end=WINDOW[1], skipped={})


def m_snap(due=NOW + timedelta(days=7), submitted=None):
    courses = [{"id": 25906, "code": "60000001", "name": "Análise Matemática I", "url": "https://moodle.example.pt/moodle/course/view.php?id=25906"},
               {"id": 25929, "code": None, "name": "Base de Dados", "url": "https://moodle.example.pt/moodle/course/view.php?id=25929"}]
    tasks = [{"external_id": "assign:1", "moodle_course": 25929, "title": "Meta3", "description": "", "due_at": due,
              "url": "https://moodle.example.pt/moodle/mod/assign/view.php?id=1", "submitted_at": submitted}]
    return MSnap(courses=courses, tasks=tasks)


def sync(source, snap, now=NOW, trigger="manual"):
    return run_sync(source, trigger, now=now, fetch=lambda _now: snap)


def test_first_import_creates_rows_but_is_not_news(session):
    run = sync("inforestudante", ir_snap())
    assert (run.status, run.stats["items"]) == ("success", {"courses": 2, "events": 2, "materials": 0})
    assert session.query(Course).count() == 2 and session.query(Event).count() == 2
    exam = session.query(Event).filter_by(kind="exam").one()
    assert exam.course.name == "Análise Matemática I"
    assert session.query(Change).filter(Change.seen_at.is_(None)).count() == 0


def test_later_changes_are_news_and_classes_are_silent(session):
    sync("inforestudante", ir_snap())
    snap = ir_snap()
    snap.events[1]["starts_at"] += timedelta(days=1)  # exam moved
    snap.events[1]["ends_at"] += timedelta(days=1)
    snap.events.append({**snap.events[0], "external_id": "aulas:3"})  # new class: silent
    sync("inforestudante", snap)
    assert [c.summary for c in session.query(Change).filter(Change.seen_at.is_(None))] == ["Data alterada: AM1 (Frequência 1)"]


def test_vanished_events_are_removed_and_exam_removal_reported(session):
    sync("inforestudante", ir_snap())
    sync("inforestudante", ir_snap(events=[ir_snap().events[0]]))
    assert session.query(Event).count() == 1
    assert [c.summary for c in session.query(Change).filter_by(kind="removed")] == ["Removido: AM1 (Frequência 1)"]


def test_empty_timetable_never_wipes_known_events(session):
    sync("inforestudante", ir_snap())
    run = sync("inforestudante", ir_snap(events=[]))
    assert run.status == "failed" and "refusing" in run.error
    assert session.query(Event).count() == 2


def test_moodle_maps_courses_by_code_or_name_and_tracks_submission(session):
    sync("inforestudante", ir_snap())
    sync("moodle", m_snap())
    task = session.query(Task).one()
    assert (task.course.name, task.status, task.kind) == ("Base de Dados", "TODO", "assignment")
    assert session.query(Course).count() == 2  # no duplicates
    assert session.query(Course).filter_by(code="60000001").one().url.endswith("id=25906")
    task.priority = 4
    session.commit()
    sync("moodle", m_snap(submitted=NOW))
    session.refresh(task)
    assert (task.status, task.progress, task.priority) == ("COMPLETED", 100, 4)


def test_course_first_seen_on_moodle_is_adopted_not_duplicated(session):
    sync("moodle", m_snap())
    sync("inforestudante", ir_snap())
    assert session.query(Course).count() == 2
    assert session.query(Task).one().course.source == "inforestudante"


def test_auth_failure_changes_nothing(session):
    def refuse(_now):
        raise AuthError("inforestudante: captcha required")

    run = run_sync("inforestudante", "scheduled", now=NOW, fetch=refuse)
    assert (run.status, run.error) == ("auth_failed", "inforestudante: captcha required")
    assert session.query(Course).count() == 0


def test_failure_mid_apply_rolls_back_everything(session):
    bad = ir_snap()
    bad.events[0]["kind"] = "not-a-kind"  # violates the check constraint after courses were written
    run = sync("inforestudante", bad)
    assert run.status == "failed"
    assert session.query(Course).count() == 0


def test_concurrent_runs_of_one_source_are_refused():
    entered, release = threading.Event(), threading.Event()

    def slow(_now):
        entered.set()
        release.wait(5)
        return ir_snap()

    t = threading.Thread(target=run_sync, args=("inforestudante", "scheduled"), kwargs={"now": NOW, "fetch": slow})
    t.start()
    entered.wait(5)
    try:
        with pytest.raises(AlreadyRunning):
            sync("inforestudante", ir_snap())
        sync("moodle", m_snap())  # other sources are independent
    finally:
        release.set()
        t.join()
    with source_lock("inforestudante"):
        pass  # released afterwards


@pytest.mark.parametrize(
    ("now", "last", "next_"),
    [
        (datetime(2026, 10, 26, 13, tzinfo=UTC), datetime(2026, 10, 26, 2, tzinfo=UTC), datetime(2026, 10, 27, 2, tzinfo=UTC)),
        (datetime(2026, 10, 26, 1, 59, tzinfo=UTC), datetime(2026, 10, 25, 2, tzinfo=UTC), datetime(2026, 10, 26, 2, tzinfo=UTC)),
        # DST ends 25 Oct 2026: 02:00 is UTC+1 on the 24th and UTC+0 on the 25th.
        (datetime(2026, 10, 24, 12, tzinfo=UTC), datetime(2026, 10, 24, 1, tzinfo=UTC), datetime(2026, 10, 25, 2, tzinfo=UTC)),
        # DST starts 29 Mar 2026: 02:00 local does exist (the gap is 01:00-02:00).
        (datetime(2026, 3, 28, 12, tzinfo=UTC), datetime(2026, 3, 28, 2, tzinfo=UTC), datetime(2026, 3, 29, 1, tzinfo=UTC)),
    ],
)
def test_schedule_is_0200_lisbon_across_dst(now, last, next_):
    tz = ZoneInfo("Europe/Lisbon")
    assert (last_slot(now, tz), next_slot(now, tz)) == (last, next_)


def add_run(session, status, minutes_ago, trigger="scheduled"):
    at = NOW - timedelta(minutes=minutes_ago)
    session.add(SyncRun(source="moodle", trigger=trigger, status=status, started_at=at, finished_at=at))
    session.commit()


def test_due_logic_catch_up_retry_and_limits(session):
    tz = ZoneInfo("Europe/Lisbon")
    assert is_due(session, "moodle", NOW, tz)  # nothing since 02:00 today: catch up
    add_run(session, "success", 60, trigger="manual")
    assert is_due(session, "moodle", NOW, tz)  # manual runs do not replace the scheduled one
    add_run(session, "failed", 5)
    assert not is_due(session, "moodle", NOW, tz)  # wait before retrying
    assert is_due(session, "moodle", NOW + timedelta(minutes=15), tz)
    add_run(session, "failed", 4)
    add_run(session, "failed", 3)
    assert not is_due(session, "moodle", NOW + timedelta(hours=1), tz)  # three attempts max
    assert is_due(session, "moodle", NOW + timedelta(days=1), tz)  # next day's slot


def test_auth_failure_is_not_retried_and_success_ends_the_day(session):
    tz = ZoneInfo("Europe/Lisbon")
    add_run(session, "auth_failed", 60)
    assert not is_due(session, "moodle", NOW + timedelta(hours=2), tz)


def test_precheck_under_lock_prevents_duplicate_scheduled_runs(session):
    tz = ZoneInfo("Europe/Lisbon")
    check = lambda s: is_due(s, "moodle", NOW, tz)  # noqa: E731
    assert run_sync("moodle", "scheduled", now=NOW, fetch=lambda _n: m_snap(), precheck=check).status == "success"
    assert run_sync("moodle", "scheduled", now=NOW, fetch=lambda _n: m_snap(), precheck=check) is None


def test_manual_sync_endpoint_starts_background_run(client, monkeypatch, session):
    from peace import sync as sync_module

    monkeypatch.setitem(sync_module.FETCHERS, "inforestudante", lambda _n: ir_snap())
    monkeypatch.setitem(sync_module.FETCHERS, "moodle", lambda _n: m_snap())
    r = client.post("/api/v1/sync")
    assert r.status_code == 202 and r.json()["started"] == ["inforestudante", "moodle"]
    assert [x.status for x in session.query(SyncRun).order_by(SyncRun.id)] == ["success", "success"]
    status = client.get("/api/v1/sync/status").json()
    assert all(not s["stale"] and s["last_status"] == "success" for s in status)


def test_settings_switch_off_a_source_and_keep_passwords_write_only(client, monkeypatch, session):
    from peace import sync as sync_module
    from peace.models import AuditEvent

    monkeypatch.setitem(sync_module.FETCHERS, "inforestudante", lambda _n: ir_snap())
    monkeypatch.setitem(sync_module.FETCHERS, "moodle", lambda _n: m_snap())
    monkeypatch.setenv("MOODLE_USERNAME", "env-user")
    monkeypatch.setenv("MOODLE_PASSWORD", "env-pass")
    monkeypatch.delenv("INFORESTUDANTE_USERNAME", raising=False)
    monkeypatch.delenv("INFORESTUDANTE_PASSWORD", raising=False)
    got = client.get("/api/v1/preferences/integrations").json()
    assert got["moodle"] == {"enabled": True, "username": "env-user", "has_password": True}
    assert got["inforestudante"]["has_password"] is False

    r = client.put("/api/v1/preferences/integrations", json={
        "inforestudante": {"enabled": False, "username": "a2020", "password": "s3cret"},
        "moodle": {"enabled": True, "username": "a2020@example.pt", "password": ""}})
    assert r.status_code == 200 and "s3cret" not in r.text and "env-pass" not in r.text
    assert sync_module._credentials("inforestudante") == ("a2020", "s3cret")
    assert sync_module._credentials("moodle") == ("a2020@example.pt", "env-pass")  # empty keeps the current password
    assert "s3cret" not in str([a.detail for a in session.query(AuditEvent)])

    assert client.post("/api/v1/sync").json()["started"] == ["moodle"]
    assert [x.source for x in session.query(SyncRun)] == ["moodle"]
    assert [s["source"] for s in client.get("/api/v1/sync/status").json()] == ["moodle"]

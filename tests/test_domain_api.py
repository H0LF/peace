from datetime import UTC, datetime, timedelta

import pytest

from peace.api import app
from peace.models import AuditEvent, Change, SyncRun, Task
from peace.routes import get_now

NOW = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)  # Monday 08:00 in Lisbon (WET, UTC+0)
API = "/api/v1"


@pytest.fixture(autouse=True)
def frozen_now():
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def at(hours=0, days=0):
    return (NOW + timedelta(days=days, hours=hours)).isoformat()


def make_course(client, name="Redes de Computadores"):
    r = client.post(f"{API}/courses", json={"name": name, "code": "RC"})
    assert r.status_code == 201, r.text
    return r.json()


def make_task(client, **kw):
    r = client.post(f"{API}/tasks", json={"title": "Trabalho", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def minutes(sessions, status=None):
    total = 0
    for s in sessions:
        if status is None or s["status"] == status:
            total += int((datetime.fromisoformat(s["ends_at"]) - datetime.fromisoformat(s["starts_at"])).seconds / 60)
    return total


def test_create_and_filter_tasks(client):
    course = make_course(client)
    t1 = make_task(client, title="Sockets TCP", kind="assignment", course_id=course["id"], due_at=at(days=1))
    make_task(client, title="Ler capítulo 3")
    assert t1["course"] == {"id": course["id"], "name": "Redes de Computadores", "code": "RC"}
    assert [t["id"] for t in client.get(f"{API}/tasks", params={"course_id": course["id"]}).json()] == [t1["id"]]
    assert [t["title"] for t in client.get(f"{API}/tasks", params={"q": "sock"}).json()] == ["Sockets TCP"]
    assert len(client.get(f"{API}/tasks", params={"status": ["TODO", "IN_PROGRESS"]}).json()) == 2


def test_search_wildcards_are_literal(client):
    make_task(client, title="Nota 100% feita")
    make_task(client, title="Outra")
    assert [t["title"] for t in client.get(f"{API}/tasks", params={"q": "%"}).json()] == ["Nota 100% feita"]


def test_complete_and_reopen_task_is_audited(client, session):
    task = make_task(client)
    done = client.patch(f"{API}/tasks/{task['id']}", json={"status": "COMPLETED"}).json()
    assert (done["progress"], datetime.fromisoformat(done["completed_at"])) == (100, NOW)
    again = client.patch(f"{API}/tasks/{task['id']}", json={"status": "TODO"}).json()
    assert (again["completed_at"], again["progress"]) == (None, 0)  # plannable again
    assert [a.detail["status"] for a in session.query(AuditEvent).order_by(AuditEvent.id)] == ["COMPLETED", "TODO"]


def test_postpone_hides_task_and_counts(client):
    task = make_task(client, due_at=at(days=2))
    assert client.post(f"{API}/tasks/{task['id']}/postpone", json={"until": at(hours=-1)}).status_code == 422
    out = client.post(f"{API}/tasks/{task['id']}/postpone", json={"until": at(days=1)}).json()
    assert out["postponed_count"] == 1
    today = client.get(f"{API}/today").json()
    assert today["priorities"] == [] and today["recommendation"] is None
    assert [t["id"] for t in client.get(f"{API}/tasks", params={"min_postponed": 1}).json()] == [task["id"]]


def test_imported_rows_cannot_be_deleted_but_manual_ones_can(client, session):
    session.add(Task(title="Do Moodle", source="moodle", external_id="a1"))
    session.commit()
    imported = session.query(Task).one()
    assert client.delete(f"{API}/tasks/{imported.id}").status_code == 409
    manual = make_task(client)
    assert client.delete(f"{API}/tasks/{manual['id']}").status_code == 204
    assert client.get(f"{API}/tasks/{manual['id']}").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"title": "x", "url": "javascript:alert(1)"},
        {"title": "x", "obsidian_note": "../../etc/passwd"},
        {"title": "x", "obsidian_note": "/home/ana/notes.md"},
        {"title": "x", "due_at": "2026-10-27T10:00:00"},  # naive datetime: ambiguous
        {"title": "x", "priority": 9},
        {"title": ""},
        {"title": "x", "unexpected": True},
        {"title": "x", "course_id": 999},
    ],
)
def test_task_input_is_validated(client, body):
    assert client.post(f"{API}/tasks", json=body).status_code == 422


def test_event_times_are_validated(client):
    bad = {"title": "Aula", "kind": "class", "starts_at": at(hours=2), "ends_at": at(hours=1)}
    assert client.post(f"{API}/events", json=bad).status_code == 422
    ok = client.post(f"{API}/events", json=bad | {"ends_at": at(hours=3)}).json()
    assert client.patch(f"{API}/events/{ok['id']}", json={"ends_at": at(hours=1)}).status_code == 422


def test_plan_day_works_around_classes_and_replanning_is_idempotent(client):
    client.post(f"{API}/events", json={"title": "Aula RC", "kind": "class", "starts_at": at(hours=1), "ends_at": at(hours=3)})
    make_task(client, title="Relatório", estimated_minutes=100, due_at=at(days=2), priority=3)
    first = client.post(f"{API}/plan/day", params={"date": "2026-10-26"}).json()
    assert first and all(datetime.fromisoformat(s["starts_at"]) >= NOW + timedelta(hours=3) for s in first)
    assert minutes(first) == 100
    assert first[0]["reason"] == "entrega em 2 dias, prioridade alta"
    again = client.post(f"{API}/plan/day", params={"date": "2026-10-26"}).json()
    assert [(s["starts_at"], s["ends_at"]) for s in again] == [(s["starts_at"], s["ends_at"]) for s in first]


def test_done_session_is_kept_and_counts_as_progress(client):
    task = make_task(client, estimated_minutes=100)
    first = client.post(f"{API}/plan/day").json()[0]
    client.patch(f"{API}/sessions/{first['id']}", json={"status": "done"})
    refreshed = client.get(f"{API}/tasks/{task['id']}").json()
    assert (refreshed["progress"], refreshed["status"]) == (50, "IN_PROGRESS")
    replanned = client.post(f"{API}/plan/day").json()
    assert minutes(replanned, "done") == 50 and minutes(replanned, "planned") == 50


def test_week_plan_spreads_work_before_the_deadline(client):
    make_task(client, estimated_minutes=300, due_at=at(days=4, hours=13))  # Friday 21:00
    week = client.post(f"{API}/plan/week").json()
    per_day = {}
    for s in week:
        assert datetime.fromisoformat(s["ends_at"]) <= NOW + timedelta(days=4, hours=13)
        day = s["starts_at"][:10]
        per_day[day] = per_day.get(day, 0) + minutes([s])
    assert sum(per_day.values()) == 300 and max(per_day.values()) <= 120


def test_planner_preferences_roundtrip_and_apply(client):
    bad = client.put(f"{API}/preferences/planner", json={"study_start": "20:00", "study_end": "08:00"})
    assert bad.status_code == 422
    assert client.put(f"{API}/preferences/planner", json={"max_daily_minutes": 60}).json()["max_daily_minutes"] == 60
    assert client.get(f"{API}/preferences/planner").json()["max_daily_minutes"] == 60
    make_task(client, estimated_minutes=600)
    make_task(client, estimated_minutes=600)
    assert minutes(client.post(f"{API}/plan/day").json()) == 60


def test_today_overview(client, session):
    make_task(client, title="Atrasada", due_at=at(hours=-5))
    make_task(client, title="Hoje", due_at=at(hours=3))
    done = make_task(client, title="Feita")
    client.patch(f"{API}/tasks/{done['id']}", json={"status": "COMPLETED"})
    client.post(f"{API}/events", json={"title": "Exame BD", "kind": "exam", "starts_at": at(days=3), "ends_at": at(days=3, hours=2)})
    session.add_all([
        Change(source="moodle", entity="task", kind="created", summary="Novo trabalho: X"),
        SyncRun(source="moodle", trigger="scheduled", status="success", finished_at=NOW),
    ])
    session.commit()
    today = client.get(f"{API}/today").json()
    assert today["date"] == "2026-10-26" and today["timezone"] == "Europe/Lisbon"
    assert [t["title"] for t in today["overdue"]] == ["Atrasada"]
    assert [t["title"] for t in today["due_today"]] == ["Hoje"]
    assert [t["title"] for t in today["completed_today"]] == ["Feita"]
    # Work due within four hours outranks overdue work (ADR-005); every consumer agrees.
    assert [p["task"]["title"] for p in today["priorities"]][:2] == ["Hoje", "Atrasada"]
    assert today["recommendation"]["title"] == "Hoje"
    assert client.get(f"{API}/recommendation").json() == today["recommendation"]
    assert [d["title"] for d in today["upcoming"]] == ["Atrasada", "Hoje", "Exame BD"]
    assert [c["summary"] for c in today["changes"]] == ["Novo trabalho: X"]
    health = {h["source"]: h for h in today["sync"]}
    assert (health["moodle"]["last_status"], health["moodle"]["stale"]) == ("success", False)
    assert (health["inforestudante"]["last_status"], health["inforestudante"]["stale"]) == (None, True)
    assert datetime.fromisoformat(health["moodle"]["next_scheduled_at"]) == datetime(2026, 10, 27, 2, tzinfo=UTC)


def test_deadlines_window_and_changes_seen(client, session):
    make_task(client, title="Longe", due_at=at(days=20))
    make_task(client, title="Perto", due_at=at(days=2))
    assert [d["title"] for d in client.get(f"{API}/deadlines", params={"days": 7}).json()] == ["Perto"]
    assert len(client.get(f"{API}/deadlines", params={"days": 30}).json()) == 2
    session.add(Change(source="moodle", entity="task", kind="created", summary="s"))
    session.commit()
    assert client.post(f"{API}/changes/seen", json={}).status_code == 204
    assert client.get(f"{API}/changes", params={"unseen": True}).json() == []


def test_appearance_preferences_validate_and_persist(client):
    assert client.get("/api/v1/preferences/appearance").json() == {"accent": "#ffb547", "name": ""}
    assert client.put("/api/v1/preferences/appearance", json={"accent": "#6fd3ff", "name": "Ana"}).status_code == 200
    assert client.get("/api/v1/preferences/appearance").json() == {"accent": "#6fd3ff", "name": "Ana"}
    for bad in ({"accent": "red"}, {"accent": "#12345"}, {"accent": "#ffb547", "name": "x" * 41}, {"accent": "#ffb547", "css": "x"}):
        assert client.put("/api/v1/preferences/appearance", json=bad).status_code == 422


def test_voice_preferences_only_accept_a_safe_key_combo(client):
    assert client.get("/api/v1/preferences/voice").json() == {"ptt": "SUPER + Y", "speak": True, "voice": "F1", "language": "en", "input_device": "", "output_device": ""}
    assert client.put("/api/v1/preferences/voice", json={"ptt": "SUPER + SHIFT + D", "speak": False, "voice": "F3"}).status_code == 200
    assert client.get("/api/v1/preferences/voice").json()["ptt"] == "SUPER + SHIFT + D"
    injection = 'SUPER + Y")) os.execute("rm'
    for bad in [injection, "SUPER+Y", "Y", "SUPER + F12", "HYPER + Y"]:
        assert client.put("/api/v1/preferences/voice", json={"ptt": bad}).status_code == 422, bad
    assert client.put("/api/v1/preferences/voice", json={"voice": "F3; rm"}).status_code == 422

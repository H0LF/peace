"""Study time (phase 2): timer, hand-entered logs, reports in Lisbon time, calibration, forgotten timers."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from peace.api import app
from peace.assistant import cmd_study_time, match_rules
from peace.models import Notification, StudyLog, StudySession, Task
from peace.notifications import focus_until, generate
from peace.planner import PlanPrefs, remaining_minutes
from peace.routes import get_now
from peace.studytime import bounds

API = "/api/v1"
LISBON = ZoneInfo("Europe/Lisbon")
T0 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)  # a Tuesday, 10:00 in Lisbon


@pytest.fixture
def clock():
    state = {"now": T0}
    app.dependency_overrides[get_now] = lambda: state["now"]
    yield state
    app.dependency_overrides.pop(get_now, None)


def make_task(client, **kw):
    course = client.post(f"{API}/courses", json={"name": kw.pop("course", "Bases de Dados"), "code": "BD"}).json()
    r = client.post(f"{API}/tasks", json={"title": "Trabalho", "course_id": course["id"], **kw})
    assert r.status_code == 201, r.text
    return r.json()


def planned_session(client, task, start=T0, minutes=60):
    r = client.post(f"{API}/sessions", json={"task_id": task["id"], "starts_at": start.isoformat(),
                                             "ends_at": (start + timedelta(minutes=minutes)).isoformat()})
    assert r.status_code == 201, r.text
    return r.json()


def log(client, start, minutes, **kw):
    return client.post(f"{API}/study-logs", json={"starts_at": start.isoformat(),
                                                  "ends_at": (start + timedelta(minutes=minutes)).isoformat(), **kw})


def test_timer_from_a_session_pauses_resumes_and_completes_it(client, session, clock):
    task = make_task(client, estimated_minutes=120)
    planned = planned_session(client, task)
    started = client.post(f"{API}/timer/start", json={"session_id": planned["id"]}).json()
    assert started["state"] == "running" and started["task"]["title"] == "Trabalho" and started["course"]["name"] == "Bases de Dados"
    assert client.post(f"{API}/timer/start", json={}).status_code == 409  # a second click starts nothing

    clock["now"] = T0 + timedelta(minutes=25)
    assert client.post(f"{API}/timer/pause").json()["state"] == "paused"
    assert client.post(f"{API}/timer/pause").status_code == 409
    clock["now"] = T0 + timedelta(minutes=40)
    assert client.get(f"{API}/timer").json()["elapsed_seconds"] == 25 * 60  # the pause does not count
    client.post(f"{API}/timer/resume")
    clock["now"] = T0 + timedelta(minutes=70)
    stopped = client.post(f"{API}/timer/stop", json={"complete_session": True}).json()
    assert (stopped["state"], stopped["logged_minutes"]) == ("idle", 55)
    session.expire_all()
    assert [round((r.ended_at - r.started_at).total_seconds() / 60) for r in session.query(StudyLog).order_by(StudyLog.id)] == [25, 30]
    assert session.get(StudySession, planned["id"]).status == "done"
    assert session.get(Task, task["id"]).progress == 50  # the existing rule: the session's share of the estimate


def test_a_pause_right_after_starting_leaves_nothing(client, session, clock):
    client.post(f"{API}/timer/start", json={})
    clock["now"] = T0 + timedelta(seconds=20)
    client.post(f"{API}/timer/pause")
    client.post(f"{API}/timer/stop", json={})
    assert session.query(StudyLog).count() == 0


def test_a_forgotten_timer_is_stopped_at_the_real_time(client, session, clock):
    client.post(f"{API}/timer/start", json={})
    clock["now"] = T0 + timedelta(hours=5)
    assert client.get(f"{API}/timer").json()["forgotten"] is True
    assert client.post(f"{API}/timer/stop", json={"ended_at": (T0 + timedelta(hours=6)).isoformat()}).status_code == 422
    assert "depois das 10:00" in client.post(f"{API}/timer/stop", json={"ended_at": T0.isoformat()}).json()["detail"]
    r = client.post(f"{API}/timer/stop", json={"ended_at": (T0 + timedelta(minutes=40)).isoformat()})
    assert r.json()["logged_minutes"] == 40


def test_the_timer_can_switch_focus_on_and_off(client, session, clock):
    client.post(f"{API}/timer/start", json={"focus": True})
    assert focus_until(session, T0) is not None
    clock["now"] = T0 + timedelta(minutes=10)
    client.post(f"{API}/timer/stop", json={})
    session.expire_all()
    assert focus_until(session, clock["now"]) is None


def test_a_session_that_is_not_planned_cannot_be_timed(client, clock):
    planned = planned_session(client, make_task(client))
    client.patch(f"{API}/sessions/{planned['id']}", json={"status": "skipped"})
    assert client.post(f"{API}/timer/start", json={"session_id": planned["id"]}).status_code == 409
    assert client.post(f"{API}/timer/start", json={"session_id": 999}).status_code == 404


def test_hand_entries_are_checked(client, clock):
    task = make_task(client)
    yesterday = T0 - timedelta(days=1)
    first = log(client, yesterday, 90, task_id=task["id"])
    assert first.status_code == 201 and first.json()["course"]["name"] == "Bases de Dados"
    assert "não se pode contar duas vezes" in log(client, yesterday + timedelta(minutes=30), 60).json()["detail"]
    assert log(client, T0 + timedelta(hours=1), 30).status_code == 422  # the future
    assert log(client, yesterday - timedelta(hours=14), 13 * 60).status_code == 422  # over 12 h
    assert log(client, yesterday, 0).status_code == 422
    assert log(client, yesterday + timedelta(minutes=90), 30).status_code == 201  # touching is not overlapping
    client.post(f"{API}/timer/start", json={})
    running = [entry for entry in client.get(f"{API}/study-time?period=day").json()["logs"] if entry["ends_at"] is None][0]
    assert client.delete(f"{API}/study-logs/{running['id']}").status_code == 409
    assert client.delete(f"{API}/study-logs/{first.json()['id']}").status_code == 204


def test_report_splits_at_local_midnight_and_compares_with_the_plan(client, clock):
    task = make_task(client)
    late = datetime(2026, 11, 2, 23, 0, tzinfo=LISBON)  # Monday 23:00 to Tuesday 01:00
    assert log(client, late, 120, task_id=task["id"]).status_code == 201
    planned_session(client, task, start=T0 + timedelta(hours=2), minutes=90)
    monday = client.get(f"{API}/study-time?period=day&date=2026-11-02").json()
    tuesday = client.get(f"{API}/study-time?period=day&date=2026-11-03").json()
    week = client.get(f"{API}/study-time?period=week&date=2026-11-05").json()
    assert (monday["actual_minutes"], tuesday["actual_minutes"], week["actual_minutes"]) == (60, 60, 120)
    assert tuesday["courses"] == [{"course_id": task["course"]["id"], "name": "Bases de Dados", "actual_minutes": 60, "planned_minutes": 90}]
    assert datetime.fromisoformat(week["start"]).date() == date(2026, 11, 2)  # weeks start on Monday
    client.post(f"{API}/timer/start", json={})
    clock["now"] = T0 + timedelta(minutes=30)
    assert client.get(f"{API}/study-time?period=day").json()["actual_minutes"] == 90  # a running timer counts


def test_bounds_follow_lisbon_days_months_and_the_dst_change():
    start, end = bounds("day", date(2026, 10, 25), LISBON)
    assert end - start == timedelta(hours=25)  # clocks went back that night
    start, end = bounds("month", date(2026, 2, 14), LISBON)
    assert (start.date(), end.date()) == (date(2026, 2, 1), date(2026, 3, 1))


def test_calibration_is_suggested_after_five_tasks_and_used_only_once_accepted(client, session, clock):
    first = make_task(client, title="Ficha 0", estimated_minutes=60)
    course = first["course"]["id"]
    tasks = [first] + [client.post(f"{API}/tasks", json={"title": f"Ficha {i}", "course_id": course, "estimated_minutes": 60}).json()
                       for i in range(1, 5)]
    for i, task in enumerate(tasks):
        assert log(client, T0 - timedelta(days=i + 1), 120, task_id=task["id"]).status_code == 201
        client.patch(f"{API}/tasks/{task['id']}", json={"status": "COMPLETED"})
    suggested = client.get(f"{API}/study-time").json()["calibration"]
    assert suggested == [{"course_id": course, "name": "Bases de Dados", "factor": 2.0, "tasks": 5, "current": None}]
    fresh = Task(title="Nova", course_id=course, estimated_minutes=60, progress=0)
    assert remaining_minutes(fresh, PlanPrefs()) == 60

    prefs = client.post(f"{API}/study-time/calibration", json={"course_id": course, "factor": 2.0}).json()
    assert prefs["effort_factors"] == {str(course): 2.0}
    assert remaining_minutes(fresh, PlanPrefs.model_validate(prefs)) == 120
    assert client.get(f"{API}/preferences/planner").json()["effort_factors"] == {str(course): 2.0}
    assert client.post(f"{API}/study-time/calibration", json={"course_id": course, "factor": 9}).status_code == 422
    assert client.post(f"{API}/study-time/calibration", json={"course_id": course, "factor": None}).json()["effort_factors"] == {}


def test_a_forgotten_timer_is_announced_once(client, session, clock):
    client.post(f"{API}/timer/start", json={"task_id": make_task(client)["id"]})
    generate(session, T0 + timedelta(hours=1), LISBON)
    assert session.query(Notification).filter(Notification.key.like("timer:%")).count() == 0
    generate(session, T0 + timedelta(hours=3, minutes=5), LISBON)
    generate(session, T0 + timedelta(hours=4), LISBON)
    note = session.query(Notification).filter(Notification.key.like("timer:%")).one()
    assert "«Trabalho»" in note.body and note.url == "/estudo"


def test_assistant_reports_study_time(client, session, clock):
    assert match_rules("quanto estudei esta semana?", T0.date()) == ("study_time", {"period": "week"})
    assert match_rules("quanto tempo estudei hoje", T0.date()) == ("study_time", {"period": "day"})
    assert cmd_study_time(session, T0).text == "Hoje ainda não registaste tempo de estudo."
    log(client, T0 - timedelta(hours=2), 80, task_id=make_task(client)["id"])
    reply = cmd_study_time(session, T0)
    assert reply.text == "Hoje estudaste 1 h 20, de 0 min planeados. Mais tempo em Bases de Dados: 1 h 20."

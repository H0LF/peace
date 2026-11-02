"""Missed-work recovery (phase 3): missed sessions and work that no longer fits become recovery options on Rever."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from peace.api import app
from peace.assistant import cmd_recover, match_rules
from peace.models import Notification, Proposal, StudySession, Task
from peace.notifications import generate
from peace.routes import get_now

API = "/api/v1"
LISBON = ZoneInfo("Europe/Lisbon")
T0 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)  # a Tuesday, 08:00 in Lisbon, before the study window


@pytest.fixture
def clock():
    app.dependency_overrides[get_now] = lambda: T0
    yield
    app.dependency_overrides.pop(get_now, None)


def course(client):
    return client.post(f"{API}/courses", json={"name": "Bases de Dados", "code": "BD"}).json()["id"]


def task(client, course_id, title, minutes, due, priority=2):
    r = client.post(f"{API}/tasks", json={"title": title, "course_id": course_id, "estimated_minutes": minutes,
                                          "due_at": due.isoformat(), "priority": priority})
    assert r.status_code == 201, r.text
    return r.json()


def missed_yesterday(session, t):
    start = T0 - timedelta(hours=22)  # Monday 10:00
    session.add(StudySession(task_id=t["id"], course_id=t["course"]["id"], starts_at=start, ends_at=start + timedelta(hours=1)))
    session.commit()


def minutes(slots, task_id=None):
    return sum((datetime.fromisoformat(s["ends_at"]) - datetime.fromisoformat(s["starts_at"])).seconds // 60
               for s in slots if task_id is None or s["task_id"] == task_id)


def test_a_missed_session_is_spread_back_and_undone(client, session, clock):
    t = task(client, course(client), "Relatório", 120, T0 + timedelta(days=3))
    missed_yesterday(session, t)
    offered = client.post(f"{API}/recovery").json()["options"]
    assert [p["kind"] for p in offered] == ["recover_spread"]  # it fits: no longer days, nothing left out
    p = offered[0]
    assert p["reason"].startswith("Ficaram por fazer 1 sessão (60 minutos)")
    assert minutes(p["preview"]["created"], t["id"]) == 120 and p["preview"]["short"] == []
    assert p["preview"]["created"][0]["title"] == "Relatório"
    assert session.query(StudySession).count() == 1  # a preview changes nothing
    assert [x["id"] for x in client.post(f"{API}/recovery").json()["options"]] == [p["id"]]  # asked twice, offered once

    applied = client.post(f"{API}/proposals/{p['id']}/apply").json()
    assert applied["status"] == "applied" and minutes(applied["result"]["created"]) == 120
    assert client.post(f"{API}/recovery").json()["options"] == []  # the work is back in the plan
    session.expire_all()
    assert session.get(Task, t["id"]).due_at == datetime.fromisoformat(t["due_at"])  # deadlines never move
    client.post(f"{API}/proposals/{p['id']}/undo")
    assert session.query(StudySession).count() == 1


def test_work_that_does_not_fit_offers_longer_days(client, session, clock):
    c = course(client)
    due = datetime(2026, 11, 4, 20, 0, tzinfo=LISBON)  # Wednesday evening: two study days of 240 min
    ids = [task(client, c, name, 200, due, priority)["id"] for name, priority in (("A", 3), ("B", 1), ("C", 1))]
    offered = {p["kind"]: p for p in client.post(f"{API}/recovery").json()["options"]}
    assert set(offered) == {"recover_spread", "recover_extend"}  # longer days fit everything: nothing must be lost
    assert "«C» não cabe antes da data" in offered["recover_spread"]["reason"]
    assert offered["recover_spread"]["preview"]["short"] and offered["recover_extend"]["preview"]["short"] == []
    ends = [datetime.fromisoformat(s["ends_at"]).astimezone(LISBON) for s in offered["recover_extend"]["preview"]["created"]]
    assert all(e.hour < 23 or (e.hour, e.minute) == (23, 0) for e in ends)  # within the recovery limit
    client.post(f"{API}/proposals/{offered['recover_extend']['id']}/apply")
    session.expire_all()
    assert session.get(Proposal, offered["recover_spread"]["id"]).status == "superseded"  # they were alternatives
    planned = session.query(StudySession).all()
    assert all(sum((s.ends_at - s.starts_at).seconds // 60 for s in planned if s.task_id == i) == 200 for i in ids)


def test_when_days_cannot_grow_the_lowest_ranked_work_is_left_out(client, session, clock):
    client.put(f"{API}/preferences/planner", json={"recovery_study_end": "21:00", "recovery_max_daily_minutes": 240})
    c = course(client)
    due = datetime(2026, 11, 4, 20, 0, tzinfo=LISBON)
    a, b, last = (task(client, c, name, 200, due, priority) for name, priority in (("A", 3), ("B", 1), ("C", 1)))
    held = StudySession(task_id=last["id"], course_id=c, starts_at=T0 + timedelta(hours=2), ends_at=T0 + timedelta(hours=3))
    session.add(held)
    session.commit()
    offered = {p["kind"]: p for p in client.post(f"{API}/recovery").json()["options"]}
    assert set(offered) == {"recover_spread", "recover_drop"}
    drop = offered["recover_drop"]["preview"]
    assert drop["dropped"] == [{"id": last["id"], "title": "C", "minutes": 200}] and drop["short"] == []
    assert [r["id"] for r in drop["removed"]] == [held.id]  # its session frees time for the rest
    assert {s["task_id"] for s in drop["created"]} == {a["id"], b["id"]}
    client.post(f"{API}/proposals/{offered['recover_drop']['id']}/apply")
    session.expire_all()
    assert session.get(Task, last["id"]).status == "TODO"  # left out of the plan, not closed
    undone = client.post(f"{API}/proposals/{offered['recover_drop']['id']}/undo").json()
    assert undone["status"] == "undone" and session.query(StudySession).filter_by(task_id=last["id"]).count() == 1


def test_nothing_missed_and_everything_fits_offers_nothing(client, session, clock):
    task(client, course(client), "Ficha", 60, T0 + timedelta(days=5))
    assert client.post(f"{API}/recovery").json()["options"] == []
    assert session.query(Proposal).count() == 0


def test_the_morning_briefing_offers_once_a_day(client, session, clock):
    missed_yesterday(session, task(client, course(client), "Relatório", 120, T0 + timedelta(days=3)))
    generate(session, T0, LISBON)
    generate(session, T0 + timedelta(minutes=1), LISBON)
    session.commit()
    assert session.query(Proposal).filter(Proposal.kind == "recover_spread").count() == 1
    briefing = session.query(Notification).filter(Notification.kind == "briefing").one()
    assert "há 1 maneira de recuperar em Rever" in briefing.body
    p = session.query(Proposal).one()
    client.post(f"{API}/proposals/{p.id}/reject")
    generate(session, T0 + timedelta(hours=1), LISBON)  # rejected today: not asked again until tomorrow
    session.commit()
    assert session.query(Proposal).count() == 1


def test_assistant_recovers_on_demand(client, session, clock):
    assert match_rules("recupera a semana", T0.date()) == ("recover", {})
    assert match_rules("fiquei atrasado", T0.date()) == ("recover", {})
    assert cmd_recover(session, T0).text == "Não há nada para recuperar: o trabalho em falta cabe no plano."
    missed_yesterday(session, task(client, course(client), "Relatório", 120, T0 + timedelta(days=3)))
    reply = cmd_recover(session, T0)
    assert reply.to == "/rever" and "1 maneira de recuperar em Rever: Recuperar no horário habitual." in reply.text


def test_work_that_cannot_fit_even_alone_is_named_but_not_replanned(client, session, clock):
    c = course(client)
    task(client, c, "Ficha urgente", 60, T0 + timedelta(hours=3))  # due 11:00, a class until then
    task(client, c, "Projeto", 120, T0 + timedelta(days=6))  # unrelated, plenty of time: must not be dragged in
    client.post(f"{API}/events", json={"title": "Aula", "kind": "class", "course_id": c,
                                       "starts_at": T0.isoformat(), "ends_at": (T0 + timedelta(hours=3)).isoformat()})
    r = client.post(f"{API}/recovery").json()
    assert r["options"] == [] and "«Ficha urgente» não cabe antes da data, nem com dias mais longos" in r["note"]
    assert "Nenhuma mudança no plano ajuda" in cmd_recover(session, T0).text

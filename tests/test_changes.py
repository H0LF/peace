from datetime import UTC, datetime, timedelta, timezone

from peace.changes import upsert_from_source
from peace.models import Change, Course, Event, Task

DUE = datetime(2026, 11, 3, 23, 59, tzinfo=UTC)


def payload(**kw):
    return {"title": "Trabalho 1 - Sockets TCP", "kind": "assignment", "due_at": DUE, "url": "https://moodle.example.pt/x"} | kw


def test_new_item_is_created_with_a_change(session):
    change = upsert_from_source(session, Task, "moodle", "assign-1", payload())
    session.commit()
    assert (change.kind, change.entity, change.summary) == ("created", "task", "Novo trabalho: Trabalho 1 - Sockets TCP")
    assert session.query(Task).one().source_data["due_at"] == DUE.isoformat()


def test_identical_payload_is_not_a_change(session):
    upsert_from_source(session, Task, "moodle", "assign-1", payload())
    assert upsert_from_source(session, Task, "moodle", "assign-1", payload()) is None
    assert session.query(Change).count() == 1


def test_same_instant_with_other_offset_is_not_a_change(session):
    upsert_from_source(session, Task, "moodle", "assign-1", payload())
    shifted = DUE.astimezone(timezone(timedelta(hours=1)))
    assert upsert_from_source(session, Task, "moodle", "assign-1", payload(due_at=shifted)) is None


def test_upstream_change_updates_only_changed_fields_and_keeps_local_edits(session):
    upsert_from_source(session, Task, "moodle", "assign-1", payload())
    task = session.query(Task).one()
    task.priority, task.title = 4, "Sockets (my title)"
    session.flush()
    later = DUE + timedelta(days=2)
    change = upsert_from_source(session, Task, "moodle", "assign-1", payload(due_at=later))
    session.commit()
    assert change.summary == "Prazo alterado: Sockets (my title)"
    assert change.detail == {"due_at": [DUE.isoformat(), later.isoformat()]}
    assert (task.due_at, task.priority, task.title) == (later, 4, "Sockets (my title)")


def test_same_external_id_in_other_source_is_separate(session):
    upsert_from_source(session, Task, "moodle", "1", payload())
    upsert_from_source(session, Task, "inforestudante", "1", payload())
    assert session.query(Task).count() == 2


def test_course_and_exam_summaries(session):
    course_change = upsert_from_source(session, Course, "inforestudante", "RC", {"name": "Redes de Computadores"})
    exam = {"title": "Exame RC", "kind": "exam", "starts_at": DUE, "ends_at": DUE + timedelta(hours=2)}
    exam_change = upsert_from_source(session, Event, "inforestudante", "ex-1", exam)
    moved = upsert_from_source(session, Event, "inforestudante", "ex-1", exam | {"starts_at": DUE + timedelta(days=1),
                                                                                  "ends_at": DUE + timedelta(days=1, hours=2)})
    assert course_change.summary == "Nova unidade curricular: Redes de Computadores"
    assert exam_change.summary == "Novo exame: Exame RC"
    assert moved.summary == "Data alterada: Exame RC"

"""Upsert rows coming from external sources and record meaningful changes (no LLM)."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from peace.models import Change, Course, Event, Material, Task

ENTITY = {Course: "course", Task: "task", Event: "event", Material: "material"}
NEW = {
    "file": "Novo material",
    "link": "Novo link",
    "course": "Nova unidade curricular",
    "task": "Nova tarefa",
    "assignment": "Novo trabalho",
    "exam": "Novo exame",
    "class": "Nova aula",
    "appointment": "Novo evento",
}


def _jsonable(value: Any) -> Any:
    # UTC so the same instant sent with a different offset is not a change.
    return value.astimezone(UTC).isoformat() if isinstance(value, datetime) else value


def upsert_from_source(
    session: Session,
    model: type[Course | Task | Event | Material],
    source: str,
    external_id: str,
    fields: dict[str, Any],
    record_created: bool = True,
) -> Change | None:
    """Create or update the row for (source, external_id).

    Only fields whose upstream value changed since the last sync are written, so local edits
    to other fields (priority, status, a corrected title...) survive. Returns the Change, or None.
    `record_created=False` stays silent about new rows (e.g. every class of a new timetable).
    """
    snapshot = {k: _jsonable(v) for k, v in fields.items()}
    obj = session.scalar(select(model).filter_by(source=source, external_id=external_id))
    if obj is None:
        obj = model(source=source, external_id=external_id, source_data=snapshot, **fields)
        session.add(obj)
        session.flush()
        return _record(session, source, obj, "created", {}) if record_created else None
    previous = obj.source_data or {}
    diff = {k: [previous.get(k), v] for k, v in snapshot.items() if previous.get(k) != v}
    if not diff:
        return None
    for k in diff:
        setattr(obj, k, fields[k])
    obj.source_data = snapshot
    session.flush()
    return _record(session, source, obj, "updated", diff)


def record_removal(session: Session, source: str, obj: Any) -> Change:
    return _record(session, source, obj, "removed", {})


def _record(session: Session, source: str, obj: Any, kind: str, diff: dict[str, Any]) -> Change:
    entity = ENTITY[type(obj)]
    title = obj.name if entity == "course" else obj.title
    if kind == "removed":
        summary = f"Removido: {title}"
    elif kind == "created":
        summary = f"{NEW['course' if entity == 'course' else obj.kind]}: {title}"
    elif "due_at" in diff:
        summary = f"Prazo alterado: {title}"
    elif "starts_at" in diff:
        summary = f"Data alterada: {title}"
    else:
        summary = f"Atualizado: {title}"
    change = Change(source=source, entity=entity, entity_id=obj.id, kind=kind, summary=summary, detail=diff)
    session.add(change)
    return change

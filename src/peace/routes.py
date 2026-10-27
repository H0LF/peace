"""Domain API: courses, tasks, events, study sessions, planning, deadlines, changes."""

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
import hashlib
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from peace import schemas as s
from peace import sync
from peace.auth import require_token
from peace.config import get_settings
from peace.db import get_session
from peace.models import (
    OPEN_STATUSES,
    PLANNABLE_STATUSES,
    AuditEvent,
    Change,
    Course,
    Event,
    Material,
    Notification,
    Preference,
    StudySession,
    SyncRun,
    Task,
)
from peace.notifications import LOW_PRIORITY, NotifyPrefs, focus_until, in_quiet_hours, low_priority_budget, notify_prefs, rate_limited
from peace import academic, knowledge, library
from peace.planner import PlanPrefs, plan_day, ranked
from peace.worker import next_slot

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
DB = Depends(get_session)


def get_now() -> datetime:
    return datetime.now(UTC)


NOW = Depends(get_now)


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(0), tz).astimezone(UTC)
    return start, end


def _minutes(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def _get[T](session: Session, model: type[T], obj_id: int) -> T:
    obj = session.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} {obj_id} not found")
    return obj


def _check_exists(session: Session, model: type, obj_id: int | None) -> None:
    if obj_id is not None and session.get(model, obj_id) is None:
        raise HTTPException(422, f"{model.__name__} {obj_id} does not exist")


def _manual_only(obj: Course | Task | Event) -> None:
    # Imported rows would be re-created by the next sync; cancel them instead.
    if obj.source != "manual":
        raise HTTPException(409, f"imported from {obj.source}; managed by synchronization")


def _apply(obj: Any, data: dict[str, Any]) -> None:
    for key, value in data.items():
        setattr(obj, key, value)


def _audit(session: Session, action: str, **detail: Any) -> None:
    session.add(AuditEvent(actor="user", action=action, detail=detail))


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _prefs(session: Session) -> PlanPrefs:
    row = session.get(Preference, "planner")
    return PlanPrefs.model_validate(row.value) if row else PlanPrefs()


def _visible_open_tasks(session: Session, now: datetime) -> list[Task]:
    query = select(Task).where(
        Task.status.in_(OPEN_STATUSES), or_(Task.snoozed_until.is_(None), Task.snoozed_until <= now)
    )
    return list(session.scalars(query.order_by(Task.due_at.asc().nulls_last(), Task.id)))


# --- audit -----------------------------------------------------------------------------------


@router.get("/audit")
def list_audit(limit: int = Query(50, ge=1, le=500), session: Session = DB):
    rows = session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit))
    return [
        {"id": r.id, "created_at": r.created_at, "actor": r.actor, "action": r.action, "detail": r.detail}
        for r in rows
    ]


# --- courses ---------------------------------------------------------------------------------


@router.get("/courses", response_model=list[s.CourseOut])
def list_courses(session: Session = DB):
    return session.scalars(select(Course).order_by(Course.name)).all()


@router.post("/courses", response_model=s.CourseOut, status_code=201)
def create_course(body: s.CourseIn, session: Session = DB):
    course = Course(**body.model_dump())
    session.add(course)
    session.commit()
    return course


@router.get("/courses/{course_id}", response_model=s.CourseOut)
def get_course(course_id: int, session: Session = DB):
    return _get(session, Course, course_id)


@router.patch("/courses/{course_id}", response_model=s.CourseOut)
def update_course(course_id: int, body: s.CoursePatch, session: Session = DB):
    course = _get(session, Course, course_id)
    _apply(course, body.model_dump(exclude_unset=True))
    session.commit()
    return course


@router.delete("/courses/{course_id}", status_code=204)
def delete_course(course_id: int, session: Session = DB):
    course = _get(session, Course, course_id)
    _manual_only(course)
    session.delete(course)
    _audit(session, "course.delete", course_id=course_id, name=course.name)
    session.commit()
    return Response(status_code=204)


# --- tasks -----------------------------------------------------------------------------------


@router.get("/tasks", response_model=list[s.TaskOut])
def list_tasks(
    status: list[s.TaskStatus] | None = Query(None),
    course_id: int | None = None,
    q: str | None = Query(None, max_length=200),
    due_after: AwareDatetime | None = None,
    due_before: AwareDatetime | None = None,
    min_postponed: int | None = Query(None, ge=1),
    limit: int = Query(200, ge=1, le=1000),
    session: Session = DB,
):
    query = select(Task)
    if status:
        query = query.where(Task.status.in_(status))
    if course_id is not None:
        query = query.where(Task.course_id == course_id)
    if q:
        query = query.where(or_(Task.title.ilike(_like(q), escape="\\"), Task.description.ilike(_like(q), escape="\\")))
    if due_after is not None:
        query = query.where(Task.due_at >= due_after)
    if due_before is not None:
        query = query.where(Task.due_at < due_before)
    if min_postponed is not None:
        query = query.where(Task.postponed_count >= min_postponed).order_by(Task.postponed_count.desc())
    query = query.order_by(Task.due_at.asc().nulls_last(), Task.priority.desc(), Task.id).limit(limit)
    return session.scalars(query).all()


@router.post("/tasks", response_model=s.TaskOut, status_code=201)
def create_task(body: s.TaskIn, session: Session = DB):
    _check_exists(session, Course, body.course_id)
    task = Task(**body.model_dump())
    session.add(task)
    session.commit()
    return task


@router.get("/tasks/{task_id}", response_model=s.TaskOut)
def get_task(task_id: int, session: Session = DB):
    return _get(session, Task, task_id)


@router.patch("/tasks/{task_id}", response_model=s.TaskOut)
def update_task(task_id: int, body: s.TaskPatch, session: Session = DB, now: datetime = NOW):
    task = _get(session, Task, task_id)
    data = body.model_dump(exclude_unset=True)
    if "course_id" in data:
        _check_exists(session, Course, data["course_id"])
    was_completed = task.status == "COMPLETED"
    _apply(task, data)
    if "status" in data:
        if task.status == "COMPLETED":
            task.completed_at = task.completed_at or now
            task.progress = 100
        else:
            task.completed_at = None
            # Reopened: 100% would make the planner treat it as finished forever.
            if was_completed and task.progress == 100 and "progress" not in data:
                task.progress = 0
        _audit(session, "task.status", task_id=task.id, status=task.status)
    session.commit()
    return task


@router.post("/tasks/{task_id}/postpone", response_model=s.TaskOut)
def postpone_task(task_id: int, body: s.PostponeIn, session: Session = DB, now: datetime = NOW):
    task = _get(session, Task, task_id)
    if body.until <= now:
        raise HTTPException(422, "until must be in the future")
    task.snoozed_until = body.until
    task.postponed_count += 1
    _audit(session, "task.postpone", task_id=task.id, until=body.until.isoformat())
    session.commit()
    return task


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, session: Session = DB):
    task = _get(session, Task, task_id)
    _manual_only(task)
    session.delete(task)
    _audit(session, "task.delete", task_id=task_id, title=task.title)
    session.commit()
    return Response(status_code=204)


# --- events (classes, exams, appointments) ---------------------------------------------------


@router.get("/events", response_model=list[s.EventOut])
def list_events(
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    kind: s.EventKind | None = None,
    course_id: int | None = None,
    session: Session = DB,
    now: datetime = NOW,
):
    start = start or _day_bounds(now.astimezone(_tz()).date(), _tz())[0]
    end = end or start + timedelta(days=7)
    query = select(Event).where(Event.starts_at < end, Event.ends_at > start)
    if kind:
        query = query.where(Event.kind == kind)
    if course_id is not None:
        query = query.where(Event.course_id == course_id)
    return session.scalars(query.order_by(Event.starts_at)).all()


@router.post("/events", response_model=s.EventOut, status_code=201)
def create_event(body: s.EventIn, session: Session = DB):
    _check_exists(session, Course, body.course_id)
    event = Event(**body.model_dump())
    session.add(event)
    session.commit()
    return event


@router.patch("/events/{event_id}", response_model=s.EventOut)
def update_event(event_id: int, body: s.EventPatch, session: Session = DB):
    event = _get(session, Event, event_id)
    data = body.model_dump(exclude_unset=True)
    if "course_id" in data:
        _check_exists(session, Course, data["course_id"])
    _apply(event, data)
    if event.ends_at < event.starts_at:
        raise HTTPException(422, "ends_at must not be before starts_at")
    session.commit()
    return event


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: int, session: Session = DB):
    event = _get(session, Event, event_id)
    _manual_only(event)
    session.delete(event)
    session.commit()
    return Response(status_code=204)


# --- study sessions and planning -------------------------------------------------------------


@router.get("/sessions", response_model=list[s.SessionOut])
def list_sessions(
    start: AwareDatetime | None = None, end: AwareDatetime | None = None, session: Session = DB, now: datetime = NOW
):
    start = start or _day_bounds(now.astimezone(_tz()).date(), _tz())[0]
    end = end or start + timedelta(days=7)
    query = select(StudySession).where(StudySession.starts_at < end, StudySession.ends_at > start)
    return _with_materials(session, list(session.scalars(query.order_by(StudySession.starts_at))))


@router.post("/sessions", response_model=s.SessionOut, status_code=201)
def create_session(body: s.SessionIn, session: Session = DB):
    session.execute(select(func.pg_advisory_xact_lock(842001)))
    _check_exists(session, Task, body.task_id)
    _check_exists(session, Course, body.course_id)
    if body.event_id is not None:
        event = _get(session, Event, body.event_id)
        if event.kind != 'exam':
            raise HTTPException(422, 'event_id must identify an assessment')
        if body.ends_at > event.starts_at:
            raise HTTPException(422, 'preparation must end before the assessment')
        if body.course_id is not None and body.course_id != event.course_id:
            raise HTTPException(422, 'assessment and session course must agree')
    row = StudySession(**body.model_dump(), reason="adicionada manualmente")
    if row.course_id is None and row.task_id is not None:
        row.course_id = session.get(Task, row.task_id).course_id
    elif row.course_id is None and row.event_id is not None:
        row.course_id = event.course_id
    session.add(row)
    session.commit()
    return row


@router.patch("/sessions/{session_id}", response_model=s.SessionOut)
def update_session(session_id: int, body: s.SessionPatch, session: Session = DB):
    session.execute(select(func.pg_advisory_xact_lock(842001)))
    row = _get(session, StudySession, session_id)
    data = body.model_dump(exclude_unset=True)
    start, end = data.get('starts_at', row.starts_at), data.get('ends_at', row.ends_at)
    status = data.get('status', row.status)
    if start is None or end is None or end <= start or status not in ('planned', 'done', 'skipped'):
        raise HTTPException(422, 'valid session status and ordered times are required')
    if row.event and status != 'skipped' and end > row.event.starts_at:
        raise HTTPException(422, 'preparation must end before the assessment')
    if row.task is not None and (row.progress_credit or 0) >= 0 and (row.status == 'done' or status == 'done'):
        task = row.task
        total = task.estimated_minutes or _prefs(session).default_task_minutes
        baseline = max(0, task.progress - (row.progress_credit or 0))
        row.progress_credit = min(100 - baseline, round(_minutes(start, end) / total * 100)) if status == 'done' else 0
        task.progress = baseline + row.progress_credit
        if task.status == "TODO":
            task.status = "IN_PROGRESS"
    _apply(row, data)
    session.commit()
    return row


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, session: Session = DB):
    session.delete(_get(session, StudySession, session_id))
    session.commit()
    return Response(status_code=204)


def _with_materials(session: Session, rows: list[StudySession]) -> list[s.SessionOut]:
    """Sessions plus the files to study for each one's task (one relevance index per request)."""
    index = knowledge.load_index(session) if rows else None
    def picks(row):
        if row.task:
            return knowledge.suggest(index, row.task)
        if row.event and row.event.course_id:
            return knowledge.suggest_text(index, row.event.title, row.event.course_id, 'exam')
        return []
    return [
        s.SessionOut.model_validate(r).model_copy(update={"materials": [s.StudyMaterial(**p) for p in picks(r)]})
        for r in rows
    ]


def _plan(session: Session, days: list[date], now: datetime) -> None:
    # Serialize planning with manual session creation so simultaneous requests cannot duplicate work.
    session.execute(select(func.pg_advisory_xact_lock(842001)))
    tz, prefs = _tz(), _prefs(session)
    tasks = academic.targets(session, now, prefs)
    # Planning is additive: an existing commitment is never silently replaced.
    allocated: dict[int, int] = defaultdict(int)
    for row in session.scalars(
        select(StudySession).where(
            StudySession.status == "planned", StudySession.ends_at > now
        )
    ):
        target_id = -row.event_id if row.event_id else row.task_id
        if target_id is not None and academic.valid_allocation(row, now):
            allocated[target_id] += _minutes(row.starts_at, row.ends_at)
    created = []
    for day in days:
        start, end = _day_bounds(day, tz)
        kept = session.scalars(
            select(StudySession).where(
                StudySession.starts_at < end, StudySession.ends_at > start, StudySession.status != "skipped"
            )
        ).all()
        events = session.scalars(select(Event).where(Event.starts_at < end, Event.ends_at > start)).all()
        busy = [(e.starts_at, e.ends_at) for e in events] + [(k.starts_at, k.ends_at) for k in kept]
        booked = sum(_minutes(k.starts_at, k.ends_at) for k in kept if k.status == "done" or k.ends_at > now)
        for p in plan_day(day, tz, prefs, tasks, busy, now, allocated, booked):
            allocated[p.target_id] += _minutes(p.starts_at, p.ends_at)
            created.append(
                StudySession(
                    task_id=p.task_id, event_id=p.event_id, course_id=p.course_id, starts_at=p.starts_at, ends_at=p.ends_at, reason=p.reason
                )
            )
    session.add_all(created)
    if created:
        session.add(Change(source='planner', entity='plan', kind='created',
                           summary=f'Plano de estudo: {len(created)} novas sessões; compromissos existentes preservados.'))
    _audit(session, "plan.generate", days=[d.isoformat() for d in days], sessions=len(created))
    session.commit()


def _sessions_between(session: Session, start: datetime, end: datetime) -> list[StudySession]:
    query = select(StudySession).where(StudySession.starts_at < end, StudySession.ends_at > start)
    return list(session.scalars(query.order_by(StudySession.starts_at)))


@router.post("/plan/day", response_model=list[s.SessionOut])
def plan_for_day(day: date | None = Query(None, alias="date"), session: Session = DB, now: datetime = NOW):
    tz = _tz()
    day = day or now.astimezone(tz).date()
    _plan(session, [day], now)
    return _with_materials(session, _sessions_between(session, *_day_bounds(day, tz)))


@router.post("/plan/week", response_model=list[s.SessionOut])
def plan_for_week(start: date | None = None, session: Session = DB, now: datetime = NOW):
    tz = _tz()
    start = start or now.astimezone(tz).date()
    days = [start + timedelta(days=i) for i in range(7)]
    _plan(session, days, now)
    return _with_materials(session, _sessions_between(session, _day_bounds(days[0], tz)[0], _day_bounds(days[-1], tz)[1]))


@router.get("/recommendation")
def get_recommendation(session: Session = DB, now: datetime = NOW):
    return academic.recommendation(session, now, _prefs(session), _tz())


# --- overview, deadlines, changes, sync, preferences -----------------------------------------


def _deadline_items(session: Session, now: datetime, days: int) -> list[dict[str, Any]]:
    horizon = now + timedelta(days=days)
    tasks = session.scalars(
        select(Task).where(Task.status.in_(OPEN_STATUSES), Task.due_at.is_not(None), Task.due_at <= horizon)
    ).all()
    exams = session.scalars(
        select(Event).where(Event.kind == "exam", Event.starts_at >= now, Event.starts_at <= horizon)
    ).all()

    def course(c: Course | None):
        return s.CourseRef.model_validate(c) if c else None

    items = [
        {"type": t.kind, "id": t.id, "title": t.title, "course": course(t.course), "due_at": t.due_at,
         "status": t.status, "overdue": t.due_at < now}
        for t in tasks
    ] + [
        {"type": "exam", "id": e.id, "title": e.title, "course": course(e.course), "due_at": e.starts_at,
         "status": None, "overdue": False}
        for e in exams
    ]
    return sorted(items, key=lambda i: i["due_at"])


@router.get("/deadlines")
def list_deadlines(days: int = Query(7, ge=1, le=120), session: Session = DB, now: datetime = NOW):
    return _deadline_items(session, now, days)


@router.get("/today")
def get_today(session: Session = DB, now: datetime = NOW):
    tz = _tz()
    day = now.astimezone(tz).date()
    start, end = _day_bounds(day, tz)
    prefs = _prefs(session)
    visible = _visible_open_tasks(session, now)
    plannable = [t for t in visible if t.status in PLANNABLE_STATUSES]
    events = session.scalars(
        select(Event).where(Event.starts_at < end, Event.ends_at > start).order_by(Event.starts_at)
    ).all()
    completed = session.scalars(
        select(Task).where(Task.completed_at >= start, Task.completed_at < end).order_by(Task.completed_at)
    ).all()
    changes = session.scalars(
        select(Change).where(Change.seen_at.is_(None)).order_by(Change.id.desc()).limit(10)
    ).all()
    task_out = s.TaskOut.model_validate
    return {
        "date": day,
        "now": now,
        "timezone": tz.key,
        "events": [s.EventOut.model_validate(e) for e in events],
        "sessions": _with_materials(session, _sessions_between(session, start, end)),
        "overdue": [task_out(t) for t in visible if t.due_at and t.due_at < now],
        "due_today": [task_out(t) for t in visible if t.due_at and now <= t.due_at < end],
        "completed_today": [task_out(t) for t in completed],
        "priorities": [
            {"task": task_out(t), "points": p, "reasons": r} for t, p, r in ranked(plannable, now, prefs)[:5]
        ],
        "recommendation": academic.recommendation(session, now, prefs, tz),
        "upcoming": _deadline_items(session, now, 7),
        "changes": [s.ChangeOut.model_validate(c) for c in changes],
        "sync": sync.status(session, now, next_slot(now, tz)),
    }


@router.get("/changes", response_model=list[s.ChangeOut])
def list_changes(unseen: bool = False, limit: int = Query(50, ge=1, le=500), session: Session = DB):
    query = select(Change)
    if unseen:
        query = query.where(Change.seen_at.is_(None))
    return session.scalars(query.order_by(Change.id.desc()).limit(limit)).all()


@router.post("/changes/seen", status_code=204)
def mark_changes_seen(body: s.SeenIn, session: Session = DB, now: datetime = NOW):
    stmt = update(Change).where(Change.seen_at.is_(None)).values(seen_at=now)
    if body.ids is not None:
        stmt = stmt.where(Change.id.in_(body.ids))
    session.execute(stmt)
    session.commit()
    return Response(status_code=204)


@router.get("/sync/runs", response_model=list[s.SyncRunOut])
def list_sync_runs(limit: int = Query(20, ge=1, le=200), session: Session = DB):
    return session.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit)).all()


@router.get("/sync/status")
def get_sync_status(session: Session = DB, now: datetime = NOW):
    return sync.status(session, now, next_slot(now, _tz()))


@router.post("/sync", status_code=202)
def start_sync(background: BackgroundTasks, session: Session = DB):
    """Manual sync of every source, in the background. The 02:00 schedule is not affected."""
    on = sync.enabled_sources(session)
    if not on:
        raise HTTPException(409, "Nenhuma integração está ligada.")
    busy = [src for src in on if sync.is_running(src)]
    if len(busy) == len(on):
        raise HTTPException(409, "A sincronização já está em curso.")
    background.add_task(sync.run_all, "manual")
    _audit(session, "sync.manual")
    session.commit()
    return {"started": [src for src in on if src not in busy], "already_running": busy}


class AppearancePrefs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accent: str = Field("#ffb547", pattern=r"^#[0-9a-fA-F]{6}$")  # the dashboard derives contrast-safe variants
    name: str = Field("", max_length=40)  # for the greeting: "Boa tarde, Ana"


# The key goes into a Lua expression on the desktop (hyprctl eval): only modifiers and one letter or digit.
PTT_PATTERN = r"^(SUPER|ALT|CTRL|SHIFT)( \+ (SUPER|ALT|CTRL|SHIFT))* \+ [A-Z0-9]$"


class VoicePrefs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ptt: str = Field("SUPER + Y", pattern=PTT_PATTERN)
    speak: bool = True  # spoken replies; off = notifications only
    voice: str = Field("F1", pattern=r"^[A-Za-z0-9_-]{1,40}$")
    language: Literal["pt", "en"] = "en"  # spoken replies; the speech engine sounds far better in English
    input_device: str = Field("", pattern=r"^(?:[A-Za-z0-9_][A-Za-z0-9_.:@/-]{0,199})?$")
    output_device: str = Field("", pattern=r"^(?:[A-Za-z0-9_][A-Za-z0-9_.:@/-]{0,199})?$")


def _save_prefs(session: Session, key: str, body: PlanPrefs | NotifyPrefs | AppearancePrefs | VoicePrefs):
    value = body.model_dump(mode="json")
    row = session.get(Preference, key)
    if row:
        row.value = value
    else:
        session.add(Preference(key=key, value=value))
    _audit(session, f"preferences.{key}", **value)
    session.commit()
    return body


@router.get("/preferences/planner", response_model=PlanPrefs)
def get_planner_prefs(session: Session = DB):
    return _prefs(session)


@router.put("/preferences/planner", response_model=PlanPrefs)
def put_planner_prefs(body: PlanPrefs, session: Session = DB):
    return _save_prefs(session, "planner", body)


@router.get("/preferences/appearance", response_model=AppearancePrefs)
def get_appearance(session: Session = DB):
    row = session.get(Preference, "appearance")
    return AppearancePrefs.model_validate(row.value) if row else AppearancePrefs()


@router.put("/preferences/appearance", response_model=AppearancePrefs)
def put_appearance(body: AppearancePrefs, session: Session = DB):
    return _save_prefs(session, "appearance", body)


@router.get("/preferences/voice", response_model=VoicePrefs)
def get_voice(session: Session = DB):
    row = session.get(Preference, "voice")
    return VoicePrefs.model_validate(row.value) if row else VoicePrefs()


@router.put("/preferences/voice", response_model=VoicePrefs)
def put_voice(body: VoicePrefs, session: Session = DB):
    return _save_prefs(session, "voice", body)


@router.get("/preferences/notifications", response_model=NotifyPrefs)
def get_notify_prefs(session: Session = DB):
    return notify_prefs(session)


@router.put("/preferences/notifications", response_model=NotifyPrefs)
def put_notify_prefs(body: NotifyPrefs, session: Session = DB):
    return _save_prefs(session, "notifications", body)


class SourceLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    url: str = Field("", max_length=300, pattern=r"^(https://[^\s/?#]+(/[^\s?#]*)?)?$")  # the school's own address
    username: str = Field("", max_length=200)
    password: str = Field("", max_length=200)  # empty: keep the saved one. Write-only, never sent back


class IntegrationsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inforestudante: SourceLogin
    moodle: SourceLogin


def _integrations_out(session: Session) -> dict[str, Any]:
    return {src: {"enabled": v["enabled"], "url": v["url"], "username": v["username"], "has_password": bool(v["password"])}
            for src, v in sync.integrations(session).items()}


@router.get("/preferences/integrations")
def get_integrations(session: Session = DB):
    return _integrations_out(session)


@router.put("/preferences/integrations")
def put_integrations(body: IntegrationsIn, session: Session = DB):
    row = session.get(Preference, "integrations")
    saved = dict(row.value) if row else {}
    for src in sync.SOURCES:
        new = getattr(body, src)
        if new.enabled and not new.url:
            raise HTTPException(422, f"Escreve o endereço do {src.capitalize()} para o ligar.")
        old = saved.get(src, {})
        saved[src] = {"enabled": new.enabled, "url": sync.site_address(src, new.url), "username": new.username.strip(),
                      "password": new.password or old.get("password", "")}
    if row:
        row.value = saved
    else:
        session.add(Preference(key="integrations", value=saved))
    # The audit trail never holds a password.
    _audit(session, "preferences.integrations",
           **{src: {"enabled": v["enabled"], "url": v["url"], "username": v["username"]} for src, v in saved.items()})
    session.commit()
    sync._moodle_token.clear()  # the next download logs in with the new account
    return _integrations_out(session)


# --- course materials ------------------------------------------------------------------------


@router.get("/materials", response_model=list[s.MaterialOut])
def list_materials(course_id: int | None = None, kind: Literal["file", "link"] | None = None, session: Session = DB):
    query = select(Material)
    if course_id is not None:
        query = query.where(Material.course_id == course_id)
    if kind:
        query = query.where(Material.kind == kind)
    return session.scalars(query.order_by(Material.course_id, Material.section, Material.title, Material.id)).all()


@router.get("/materials/{material_id}/file")
def material_file(material_id: int, session: Session = DB):
    """The server's copy of the file (the worker downloads it; the desktop mirrors it into the vault)."""
    material = _get(session, Material, material_id)
    path = library.cached_file(material.id)
    if material.kind != "file" or not path.is_file():
        raise HTTPException(404, "ficheiro ainda não descarregado")
    return FileResponse(path, media_type=material.mimetype or "application/octet-stream", filename=material.filename)


@router.get("/tasks/{task_id}/materials", response_model=list[s.StudyMaterial])
def task_materials(task_id: int, session: Session = DB):
    return knowledge.suggest(knowledge.load_index(session), _get(session, Task, task_id))


# --- knowledge graph -------------------------------------------------------------------------


@router.get("/knowledge/graph")
def knowledge_graph(course_id: int | None = None, session: Session = DB, now: datetime = NOW):
    G = knowledge.build_graph(session, now)
    return knowledge.graph_json(G, knowledge.communities(G), course_id)


def _notes_dir() -> Path:
    return (library.data_dir() / "graphify" / "obsidian").resolve()


@router.get("/knowledge/notes")
def knowledge_notes():
    """Graphify's Obsidian notes for the desktop to copy (only .md and .canvas, never .obsidian/)."""
    root = _notes_dir()
    if not root.is_dir():
        return []
    files = (p for p in root.rglob("*") if p.is_file() and p.suffix in (".md", ".canvas"))
    return [
        {"path": str(p.relative_to(root)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in files
        if not any(part.startswith(".") for part in p.relative_to(root).parts)
    ]


@router.get("/knowledge/notes/file")
def knowledge_note(path: str = Query(max_length=500)):
    root = _notes_dir()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or target.suffix not in (".md", ".canvas") or not target.is_file():
        raise HTTPException(404, "nota não encontrada")
    return FileResponse(target, media_type="text/markdown" if target.suffix == ".md" else "application/json")


# --- desktop notifications -------------------------------------------------------------------


@router.get("/notifications/pending", response_model=list[s.NotificationOut])
def pending_notifications(session: Session = DB, now: datetime = NOW):
    """What the desktop should show now. Empty during quiet hours; only urgent ones in focus mode;
    expired ones are never shown."""
    prefs = notify_prefs(session)
    held = not prefs.enabled or in_quiet_hours(now, _tz(), prefs)
    query = select(Notification).where(
        Notification.delivered_at.is_(None),
        Notification.deliver_after <= now,
        or_(Notification.expires_at.is_(None), Notification.expires_at > now),
    )
    if held:
        query = query.where(Notification.kind == "test")  # an explicit test bypasses automatic-delivery settings
    if focus_until(session, now):
        query = query.where(or_(Notification.urgent, Notification.kind == "test"))
    allowed = low_priority_budget(session, now, prefs)
    if allowed <= 0:  # filtered in SQL, so a backlog of them cannot fill the 20 rows and hide a reminder
        query = query.where(or_(Notification.kind.not_in(LOW_PRIORITY), Notification.urgent))
    rows = session.scalars(query.order_by(Notification.urgent.desc(), Notification.kind.in_(LOW_PRIORITY), Notification.id)).yield_per(100)
    return rate_limited((n for n in rows if _notification_is_current(n, session, prefs, now)), allowed, prefs)


def _notification_is_current(n: Notification, session: Session, prefs: NotifyPrefs, now: datetime) -> bool:
    category = {"deadline": "deadlines", "exam": "deadlines", "session": "sessions", "changes": "changes",
                "missed": "missed", "integration": "integrations"}.get(n.kind)
    if category and not getattr(prefs, category):
        return False
    if n.task_id:
        task = session.get(Task, n.task_id)
        if not task or task.status not in OPEN_STATUSES:
            return False
        if n.kind == "session" and (task.progress or 0) >= 100:
            return False
        if n.kind == "deadline" and (task.due_at != n.expires_at or not task.due_at or task.due_at <= now):
            return False
    if n.session_id:
        study = session.get(StudySession, n.session_id)
        if not study or study.status != "planned" or study.ends_at != n.expires_at or study.ends_at <= now:
            return False
        if study.event_id and (not study.event or study.event.starts_at <= now or study.ends_at > study.event.starts_at):
            return False
    if n.kind == "exam" and n.key.startswith("exam:"):
        exam = session.get(Event, int(n.key.split(":")[1]))
        if not exam or exam.kind != "exam" or exam.starts_at != n.expires_at or exam.starts_at <= now:
            return False
    return True


class NotificationDeliveryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notify_send: bool | None = None
    session_bus: bool | None = None
    daemon_available: bool | None = None
    daemon: str | None = Field(None, max_length=250)
    notification_id: int | None = Field(None, gt=0)
    native_id: int | None = Field(None, gt=0)
    result: Literal["accepted", "error"] | None = None


@router.post("/desktop/notifications/status", status_code=204)
def notification_delivery_status(body: NotificationDeliveryIn, session: Session = DB, now: datetime = NOW):
    row = session.get(Preference, "notification_delivery")
    value = {**(row.value if row else {}), **body.model_dump(exclude_unset=True), "seen_at": now.isoformat()}
    if row:
        row.value = value
    else:
        session.add(Preference(key="notification_delivery", value=value))
    session.commit()


@router.get("/notifications/diagnostics")
def notification_diagnostics(session: Session = DB, now: datetime = NOW):
    desktop, delivery = session.get(Preference, "desktop"), session.get(Preference, "notification_delivery")
    seen = datetime.fromisoformat(desktop.value["seen_at"]) if desktop and desktop.value.get("seen_at") else None
    checked = datetime.fromisoformat(delivery.value["seen_at"]) if delivery and delivery.value.get("seen_at") else None
    prefs = notify_prefs(session)
    return {"enabled": prefs.enabled, "quiet_hours": in_quiet_hours(now, _tz(), prefs),
            "focus_active": focus_until(session, now) is not None,
            "desktop_connected": bool(seen and now - seen < timedelta(seconds=90)),
            "delivery_connected": bool(checked and now - checked < timedelta(seconds=90)),
            "delivery": delivery.value if delivery else None,
            "pending": len(pending_notifications(session, now)), "visual_confirmation": "pending_user"}


@router.post("/notifications/test", response_model=s.NotificationOut, status_code=201)
def test_native_notification(session: Session = DB, now: datetime = NOW):
    """Queue a real desktop test; only a connected native agent can display it."""
    # One test per minute limits repeated clicks; an explicit test is allowed in quiet hours.
    key = f"test:{now:%Y%m%d%H%M}"
    from peace.notifications import _add
    _add(session, key=key, kind="test", title="PEACE — teste de notificação nativa",
         body="Esta mensagem veio do agente PEACE pelo serviço de notificações do Linux.",
         url="/definicoes", actions=["open"], deliver_after=now.replace(second=0, microsecond=0),
         expires_at=now.replace(second=0, microsecond=0) + timedelta(minutes=2))
    _audit(session, "notifications.test")
    session.commit()
    return session.scalar(select(Notification).where(Notification.key == key))


@router.post("/notifications/{notification_id}/delivered", status_code=204)
def notification_delivered(notification_id: int, session: Session = DB, now: datetime = NOW):
    row = _get(session, Notification, notification_id)
    row.delivered_at = row.delivered_at or now
    session.commit()
    return Response(status_code=204)


@router.post("/notifications/{notification_id}/snooze", status_code=204)
def snooze_notification(notification_id: int, body: s.SnoozeIn, session: Session = DB, now: datetime = NOW):
    row = _get(session, Notification, notification_id)
    row.delivered_at, row.deliver_after = None, now + timedelta(minutes=body.minutes)
    session.commit()
    return Response(status_code=204)


@router.get("/notifications", response_model=list[s.NotificationOut])
def list_notifications(limit: int = Query(50, ge=1, le=500), session: Session = DB):
    return session.scalars(select(Notification).order_by(Notification.id.desc()).limit(limit)).all()

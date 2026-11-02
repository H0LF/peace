"""Study time (phase 2): a timer and hand-entered Study logs, planned vs actual reports, effort calibration.

Time studied is never evidence of learning. A running timer is the open timer `StudyLog`; a paused one is the
context kept in the `timer` preference with no open log. Days, weeks and months use the configured timezone.
"""

from datetime import UTC, date, datetime, time, timedelta
from statistics import median
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from peace import routes
from peace import schemas as s
from peace.auth import require_token
from peace.db import get_session
from peace.models import Course, Preference, StudyLog, StudySession, Task
from peace.planner import PlanPrefs

FORGOTTEN = timedelta(hours=3)  # a timer running this long gets a "still studying?" nudge
MAX_MANUAL = timedelta(hours=12)
MIN_STRETCH = timedelta(minutes=1)  # a pause right after starting leaves no row
CALIBRATE_AFTER = 5  # completed tasks per course before suggesting a factor

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
DB = Depends(get_session)
NOW = Depends(routes.get_now)


class TimerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: int | None = None
    task_id: int | None = None
    course_id: int | None = None
    focus: bool = False


class StopIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ended_at: AwareDatetime | None = None  # "I stopped at 16:40" for a forgotten timer
    complete_session: bool = False


class LogIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    task_id: int | None = None
    course_id: int | None = None


class CalibrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: int
    factor: float | None  # None: stop using one


# --- timer -------------------------------------------------------------------------------------


def _context(session: Session) -> dict[str, Any]:
    row = session.get(Preference, "timer")
    return dict(row.value) if row and row.value else {}


def _set_context(session: Session, value: dict[str, Any] | None) -> None:
    row = session.get(Preference, "timer")
    if value is None:
        if row:
            session.delete(row)
    elif row:
        row.value = value
    else:
        session.add(Preference(key="timer", value=value))


def _running(session: Session) -> StudyLog | None:
    return session.scalar(select(StudyLog).where(StudyLog.ended_at.is_(None)))


def _close(session: Session, run: StudyLog, end: datetime, ctx: dict[str, Any]) -> None:
    """End the running stretch and add it to the timer's total (a stretch under a minute is dropped)."""
    if end - run.started_at < MIN_STRETCH:
        session.delete(run)
        return
    run.ended_at = end
    ctx["seconds"] = ctx.get("seconds", 0) + int((end - run.started_at).total_seconds())


def timer_state(session: Session, now: datetime) -> dict[str, Any]:
    ctx, run = _context(session), _running(session)
    if not ctx and not run:
        return {"state": "idle"}
    if run and not ctx:  # the context was lost: rebuild it from the row itself
        ctx = {"task_id": run.task_id, "course_id": run.course_id, "session_id": run.session_id,
               "seconds": 0, "since": run.started_at.isoformat()}
    task = session.get(Task, ctx["task_id"]) if ctx.get("task_id") else None
    course = session.get(Course, ctx["course_id"]) if ctx.get("course_id") else None
    return {
        "state": "running" if run else "paused",
        "task": task and {"id": task.id, "title": task.title},
        "course": course and {"id": course.id, "name": course.name},
        "session_id": ctx.get("session_id"),
        "focus": bool(ctx.get("focus")),
        "since": ctx.get("since"),
        "running_since": run.started_at if run else None,
        "banked_seconds": ctx.get("seconds", 0),  # before the current stretch, so the dashboard can tick locally
        "elapsed_seconds": ctx.get("seconds", 0) + (int((now - run.started_at).total_seconds()) if run else 0),
        "forgotten": bool(run and now - run.started_at >= FORGOTTEN),
    }


def _lock(session: Session) -> None:
    session.execute(select(func.pg_advisory_xact_lock(842002)))  # two clicks on "Começar" start one timer


@router.get("/timer")
def get_timer(session: Session = DB, now: datetime = NOW):
    return timer_state(session, now)


@router.post("/timer/start")
def start_timer(body: TimerIn, session: Session = DB, now: datetime = NOW):
    _lock(session)
    if _context(session) or _running(session):
        raise HTTPException(409, "Já há um cronómetro ligado; termina-o primeiro.")
    task_id, course_id, minutes = body.task_id, body.course_id, 50
    if body.session_id is not None:
        planned = routes._get(session, StudySession, body.session_id)
        if planned.status != "planned":
            raise HTTPException(409, "Esta sessão já não está planeada.")
        task_id, course_id = planned.task_id, planned.course_id
        minutes = max(15, routes._minutes(max(now, planned.starts_at), planned.ends_at))
    elif task_id is not None:
        course_id = routes._get(session, Task, task_id).course_id
    elif course_id is not None:
        routes._check_exists(session, Course, course_id)
    session.add(StudyLog(session_id=body.session_id, task_id=task_id, course_id=course_id, started_at=now, origin="timer"))
    _set_context(session, {"session_id": body.session_id, "task_id": task_id, "course_id": course_id,
                           "focus": body.focus, "seconds": 0, "since": now.isoformat()})
    routes._audit(session, "timer.start", task_id=task_id, course_id=course_id, session_id=body.session_id)
    if body.focus:
        from peace.assistant import set_focus  # the assistant imports the routes; importing it at module level would cycle

        set_focus(session, now, minutes)  # commits
    session.commit()
    return timer_state(session, now)


@router.post("/timer/pause")
def pause_timer(session: Session = DB, now: datetime = NOW):
    _lock(session)
    run, ctx = _running(session), _context(session)
    if run is None:
        raise HTTPException(409, "O cronómetro não está a contar.")
    _close(session, run, now, ctx)
    _set_context(session, ctx)
    session.commit()
    return timer_state(session, now)


@router.post("/timer/resume")
def resume_timer(session: Session = DB, now: datetime = NOW):
    _lock(session)
    ctx = _context(session)
    if not ctx or _running(session):
        raise HTTPException(409, "Não há um cronómetro em pausa.")
    session.add(StudyLog(session_id=ctx.get("session_id"), task_id=ctx.get("task_id"), course_id=ctx.get("course_id"),
                         started_at=now, origin="timer"))
    session.commit()
    return timer_state(session, now)


@router.post("/timer/stop")
def stop_timer(body: StopIn, session: Session = DB, now: datetime = NOW):
    _lock(session)
    run, ctx = _running(session), _context(session)
    if run is None and not ctx:
        raise HTTPException(409, "Não há cronómetro ligado.")
    if run is not None:
        end = body.ended_at or now
        if end > now:
            raise HTTPException(422, "A hora em que paraste não pode ser no futuro.")
        if end <= run.started_at:
            raise HTTPException(422, f"A hora em que paraste tem de ser depois das {run.started_at.astimezone(routes._tz()):%H:%M}.")
        _close(session, run, end, ctx)
    minutes = round(ctx.get("seconds", 0) / 60)
    _set_context(session, None)
    routes._audit(session, "timer.stop", minutes=minutes, session_id=ctx.get("session_id"))
    if ctx.get("focus"):
        from peace.assistant import set_focus

        set_focus(session, now, None)
    session.commit()
    if body.complete_session and ctx.get("session_id"):
        planned = session.get(StudySession, ctx["session_id"])
        if planned is not None and planned.status == "planned":
            routes.update_session(planned.id, s.SessionPatch(status="done"), session)
    return {**timer_state(session, now), "logged_minutes": minutes}


# --- hand-entered logs -------------------------------------------------------------------------


def _log_out(log: StudyLog, now: datetime) -> dict[str, Any]:
    end = log.ended_at or now
    return {"id": log.id, "starts_at": log.started_at, "ends_at": log.ended_at, "origin": log.origin,
            "minutes": round((end - log.started_at).total_seconds() / 60), "session_id": log.session_id,
            "task": log.task and {"id": log.task.id, "title": log.task.title},
            "course": log.course and {"id": log.course.id, "name": log.course.name}}


@router.post("/study-logs", status_code=201)
def add_log(body: LogIn, session: Session = DB, now: datetime = NOW):
    if body.ends_at <= body.starts_at:
        raise HTTPException(422, "O fim tem de ser depois do início.")
    if body.ends_at > now + timedelta(minutes=1):
        raise HTTPException(422, "Só podes registar tempo que já passou.")
    if body.ends_at - body.starts_at > MAX_MANUAL:
        raise HTTPException(422, "No máximo 12 horas de cada vez.")
    course_id = body.course_id
    if body.task_id is not None:
        course_id = routes._get(session, Task, body.task_id).course_id
    elif course_id is not None:
        routes._check_exists(session, Course, course_id)
    _lock(session)
    clash = session.scalar(select(StudyLog).where(StudyLog.started_at < body.ends_at,
                                                  func.coalesce(StudyLog.ended_at, now) > body.starts_at).limit(1))
    if clash is not None:
        tz = routes._tz()
        raise HTTPException(409, f"Já tens tempo registado das {clash.started_at.astimezone(tz):%H:%M} às "
                                 f"{(clash.ended_at or now).astimezone(tz):%H:%M}; não se pode contar duas vezes.")
    log = StudyLog(task_id=body.task_id, course_id=course_id, started_at=body.starts_at, ended_at=body.ends_at, origin="manual")
    session.add(log)
    routes._audit(session, "study_log.add", minutes=round((body.ends_at - body.starts_at).total_seconds() / 60))
    session.commit()
    return _log_out(log, now)


@router.delete("/study-logs/{log_id}", status_code=204)
def delete_log(log_id: int, session: Session = DB):
    log = routes._get(session, StudyLog, log_id)
    if log.ended_at is None:
        raise HTTPException(409, "Este tempo ainda está a contar: termina o cronómetro primeiro.")
    session.delete(log)
    routes._audit(session, "study_log.delete", id=log_id)
    session.commit()
    return Response(status_code=204)


# --- reports and calibration -------------------------------------------------------------------


def bounds(period: str, day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    if period == "week":
        first = day - timedelta(days=day.weekday())
        last = first + timedelta(days=7)
    elif period == "month":
        first = day.replace(day=1)
        last = (first + timedelta(days=32)).replace(day=1)
    else:
        first, last = day, day + timedelta(days=1)
    # In UTC: subtracting two times that share one ZoneInfo ignores a DST change between them.
    return datetime.combine(first, time(0), tz).astimezone(UTC), datetime.combine(last, time(0), tz).astimezone(UTC)


def calibration(session: Session) -> list[dict[str, Any]]:
    """Per course with enough finished tasks: median of actual ÷ estimated time, and the factor in use."""
    prefs = routes._prefs(session)
    spent = dict(session.execute(select(StudyLog.task_id, func.sum(func.extract("epoch", StudyLog.ended_at - StudyLog.started_at)))
                                 .where(StudyLog.task_id.is_not(None), StudyLog.ended_at.is_not(None))
                                 .group_by(StudyLog.task_id)).all())
    ratios: dict[int, list[float]] = {}
    for task in session.scalars(select(Task).where(Task.status == "COMPLETED", Task.id.in_(spent or [-1]), Task.course_id.is_not(None))):
        estimate = task.estimated_minutes or prefs.default_task_minutes
        ratios.setdefault(task.course_id, []).append(float(spent[task.id]) / 60 / estimate)
    out = []
    for course_id, values in ratios.items():
        current = prefs.effort_factors.get(course_id)
        factor = min(4.0, max(0.5, round(median(values), 1)))
        if len(values) >= CALIBRATE_AFTER and (abs(factor - 1) >= 0.15 or current):
            out.append({"course_id": course_id, "name": session.get(Course, course_id).name, "factor": factor,
                        "tasks": len(values), "current": current})
    return sorted(out, key=lambda c: c["name"])


def report(session: Session, period: str, day: date, now: datetime) -> dict[str, Any]:
    start, end = bounds(period, day, routes._tz())
    logs = session.scalars(select(StudyLog).where(StudyLog.started_at < end, func.coalesce(StudyLog.ended_at, now) > start)
                           .order_by(StudyLog.started_at.desc())).all()
    actual: dict[int | None, float] = {}
    for log in logs:
        clipped = (min(end, log.ended_at or now) - max(start, log.started_at)).total_seconds()
        actual[log.course_id] = actual.get(log.course_id, 0) + max(0.0, clipped)
    planned: dict[int | None, float] = {}
    for row in session.scalars(select(StudySession).where(StudySession.starts_at < end, StudySession.ends_at > start,
                                                          StudySession.status != "skipped")):
        planned[row.course_id] = planned.get(row.course_id, 0) + (row.ends_at - row.starts_at).total_seconds()
    courses = []
    for course_id in sorted(set(actual) | set(planned), key=lambda c: (c is None, c or 0)):
        course = session.get(Course, course_id) if course_id else None
        courses.append({"course_id": course_id, "name": course.name if course else "Sem cadeira",
                        "actual_minutes": round(actual.get(course_id, 0) / 60),
                        "planned_minutes": round(planned.get(course_id, 0) / 60)})
    return {"period": period, "start": start, "end": end,
            "actual_minutes": round(sum(actual.values()) / 60), "planned_minutes": round(sum(planned.values()) / 60),
            "courses": courses, "logs": [_log_out(log, now) for log in logs], "calibration": calibration(session)}


@router.get("/study-time")
def get_report(period: Literal["day", "week", "month"] = "week", day: date | None = Query(None, alias="date"),
               session: Session = DB, now: datetime = NOW):
    return report(session, period, day or now.astimezone(routes._tz()).date(), now)


@router.post("/study-time/calibration", response_model=PlanPrefs)
def set_calibration(body: CalibrationIn, session: Session = DB):
    routes._check_exists(session, Course, body.course_id)
    prefs = routes._prefs(session)
    factors = {k: v for k, v in prefs.effort_factors.items() if k != body.course_id}
    if body.factor is not None:
        factors[body.course_id] = body.factor
    try:
        updated = PlanPrefs.model_validate({**prefs.model_dump(), "effort_factors": factors})
    except ValueError:
        raise HTTPException(422, "O fator tem de estar entre 0,5 e 4.") from None
    return routes._save_prefs(session, "planner", updated)


def running_too_long(session: Session, now: datetime) -> StudyLog | None:
    run = _running(session)
    return run if run and now - run.started_at >= FORGOTTEN else None

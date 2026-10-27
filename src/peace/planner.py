"""Deterministic study planner: pure functions, no database, no clock, no LLM.

Tasks are ranked by explainable points (deadline, priority, remaining work, postponements),
then placed into free time inside the study window, around classes/exams and existing sessions.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

MIN_BLOCK = 15
PRIORITY_POINTS = {1: 0, 2: 10, 3: 25, 4: 140}
PRIORITY_REASON = {3: "prioridade alta", 4: "prioridade crítica"}


class PlanPrefs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    study_start: time = time(9, 0)
    study_end: time = time(21, 0)
    max_daily_minutes: int = Field(240, ge=15, le=720)
    block_minutes: int = Field(50, ge=15, le=180)
    break_minutes: int = Field(10, ge=0, le=60)
    max_task_minutes_per_day: int = Field(120, ge=15, le=480)
    default_task_minutes: int = Field(60, ge=15, le=600)
    default_exam_minutes: int = Field(120, ge=15, le=600)

    @model_validator(mode="after")
    def _window_is_ordered(self):
        if self.study_end <= self.study_start:
            raise ValueError("study_end must be after study_start")
        return self


@dataclass(frozen=True)
class Proposal:
    task_id: int | None
    course_id: int | None
    starts_at: datetime
    ends_at: datetime
    reason: str
    event_id: int | None = None

    @property
    def target_id(self) -> int:
        return -self.event_id if self.event_id is not None else self.task_id


def assessment_targets(events: list[Any], completed: dict[int, int], prefs: PlanPrefs) -> list[Any]:
    """A policy preparation budget, not an estimate of readiness or assessment importance.

    Negative internal IDs keep event and task allocation distinct. No synthetic tasks are persisted.
    """
    return [SimpleNamespace(
        id=-e.id, event_id=e.id, title=e.title, course_id=e.course_id, course=getattr(e, 'course', None),
        due_at=e.starts_at, kind='exam', priority=2, progress=0, postponed_count=0,
        estimated_minutes=max(0, prefs.default_exam_minutes - completed.get(e.id, 0)),
        description='', status='TODO',
    ) for e in events if completed.get(e.id, 0) < prefs.default_exam_minutes]


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def remaining_minutes(task: Any, prefs: PlanPrefs) -> int:
    total = task.estimated_minutes or prefs.default_task_minutes
    return round(total * (100 - (task.progress or 0)) / 100)


def score(task: Any, now: datetime, prefs: PlanPrefs) -> tuple[int, list[str]]:
    priority = task.priority or 2
    points = PRIORITY_POINTS[priority]
    reasons = [PRIORITY_REASON[priority]] if priority in PRIORITY_REASON else []
    assessment = getattr(task, 'kind', '') == 'exam'
    if assessment:
        points += 35
        reasons.append('preparação para avaliação; peso e preparação atual desconhecidos')
    if task.due_at is not None:
        hours = (task.due_at - now).total_seconds() / 3600
        if hours < 0:
            points += 100
            reasons.insert(0, "em atraso")
        elif hours < 24:
            points += 60
            reasons.insert(0, "entrega em menos de 24 horas")
        elif hours < 72:
            points += 40
            reasons.insert(0, f"entrega em {_plural(max(1, round(hours / 24)), 'dia', 'dias')}")
        elif hours < 168:
            points += 20
            reasons.insert(0, "entrega esta semana")
        elif hours < 336:
            points += 10
        if not assessment and 0 <= hours < 4:
            points += 60
            reasons.append('prazo iminente')
        if assessment:
            reasons = [r.replace('entrega', 'avaliação') for r in reasons]
        # Needing 10%+ of all remaining clock time means it cannot wait.
        if hours > 0 and remaining_minutes(task, prefs) / 60 >= hours * 0.1:
            points += 15
            reasons.append("muito trabalho para o tempo que resta")
    if task.postponed_count:
        points += 5 * min(task.postponed_count, 4)
        reasons.append(f"adiada {_plural(task.postponed_count, 'vez', 'vezes')}")
    return points, reasons


def ranked(tasks: list[Any], now: datetime, prefs: PlanPrefs) -> list[tuple[Any, int, list[str]]]:
    scored = [(t, *score(t, now, prefs)) for t in tasks
              if remaining_minutes(t, prefs) > 0 and getattr(t, 'status', 'TODO') in ('TODO', 'IN_PROGRESS')
              and not (getattr(t, 'kind', '') == 'exam' and t.due_at <= now)]
    far = datetime.max.replace(tzinfo=UTC)
    return sorted(scored, key=lambda s: (-s[1], s[0].due_at or far, s[0].id))


def recommend(tasks: list[Any], now: datetime, prefs: PlanPrefs) -> dict[str, Any] | None:
    top = ranked(tasks, now, prefs)
    if not top:
        return None
    task, _, reasons = top[0]
    minutes = max(MIN_BLOCK, min(prefs.block_minutes, remaining_minutes(task, prefs)))
    why = f" ({', '.join(reasons)})" if reasons else ""
    return {
        "task_id": None if getattr(task, 'event_id', None) else task.id,
        "event_id": getattr(task, 'event_id', None),
        "title": task.title,
        "minutes": minutes,
        "reasons": reasons,
        "text": f"Recomendado: {minutes} minutos em «{task.title}»{why}.",
        "readiness": None,
        "assessment_weight": None,
        "estimated_minutes": None if getattr(task, 'event_id', None) else task.estimated_minutes,
        "objective": 'Rever a matéria e resolver exercícios ou avaliações anteriores; identificar dúvidas.'
            if getattr(task, 'event_id', None) else 'Avançar no trabalho restante da tarefa.',
    }


def window(day: date, tz: ZoneInfo, prefs: PlanPrefs) -> tuple[datetime, datetime]:
    """The study window of a local day, in UTC (DST-safe: offsets come from that day)."""
    start = datetime.combine(day, prefs.study_start, tz).astimezone(UTC)
    end = datetime.combine(day, prefs.study_end, tz).astimezone(UTC)
    return start, end


def free_slots(
    day: date, tz: ZoneInfo, prefs: PlanPrefs, busy: list[tuple[datetime, datetime]], now: datetime
) -> list[tuple[datetime, datetime]]:
    start, end = window(day, tz, prefs)
    # Round "now" up to the next 5 minutes so sessions start on a readable time.
    soon = (now + timedelta(minutes=4, seconds=59)).replace(second=0, microsecond=0)
    cursor = max(start, soon - timedelta(minutes=soon.minute % 5))
    slots = []
    for b_start, b_end in sorted(busy):
        if b_end <= cursor or b_start >= end:
            continue
        if b_start > cursor:
            slots.append((cursor, min(b_start, end)))
        cursor = max(cursor, b_end)
    if cursor < end:
        slots.append((cursor, end))
    return [(s, e) for s, e in slots if _minutes(e - s) >= MIN_BLOCK]


def plan_day(
    day: date,
    tz: ZoneInfo,
    prefs: PlanPrefs,
    tasks: list[Any],
    busy: list[tuple[datetime, datetime]],
    now: datetime,
    allocated: dict[int, int] | None = None,
    booked_minutes: int = 0,
) -> list[Proposal]:
    """Propose sessions for one day. `allocated` = minutes already planned per task on other days;
    `booked_minutes` = study minutes already on this day (kept sessions)."""
    allocated = allocated or {}
    slots = free_slots(day, tz, prefs, busy, now)
    budget = prefs.max_daily_minutes - booked_minutes
    day_start, _ = window(day, tz, prefs)
    out: list[Proposal] = []
    ordered = ranked(tasks, max(now, day_start), prefs)
    exams = [t for t, _, _ in ordered if getattr(t, 'event_id', None)
             and remaining_minutes(t, prefs) > allocated.get(t.id, 0)
             and day_start < t.due_at <= max(now, day_start) + timedelta(days=3)]
    # Reserve a useful block for each upcoming assessment when time is scarce.
    exam_cap = max(MIN_BLOCK, min(budget, sum(_minutes(e - s) for s, e in slots)) // max(1, len(exams)))
    for task, _, reasons in ordered:
        if task.due_at is not None and now < task.due_at <= day_start:
            continue  # the deadline will already have passed on this day
        want = min(remaining_minutes(task, prefs) - allocated.get(task.id, 0), prefs.max_task_minutes_per_day)
        if task in exams:
            want = min(want, exam_cap)
        while want >= MIN_BLOCK and budget >= MIN_BLOCK and slots:
            start, end = slots[0]
            # Absorb a tail shorter than one block instead of leaving it unplannable.
            available = min(want, budget)
            length = available if available - prefs.block_minutes < MIN_BLOCK else prefs.block_minutes
            length = min(length, _minutes(end - start))
            if task.due_at is not None and task.due_at > now:
                length = min(length, _minutes(task.due_at - start))
            if length < MIN_BLOCK:
                break
            stop = start + timedelta(minutes=length)
            event_id = getattr(task, 'event_id', None)
            out.append(Proposal(None if event_id else task.id, task.course_id, start, stop, ", ".join(reasons), event_id))
            want -= length
            budget -= length
            nxt = stop + timedelta(minutes=prefs.break_minutes)
            if _minutes(end - nxt) >= MIN_BLOCK:
                slots[0] = (nxt, end)
            else:
                slots.pop(0)
    return out

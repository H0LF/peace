"""Shared academic candidates and recommendations for API, planner, assistant and briefing."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from peace import knowledge
from peace.models import Event, PLANNABLE_STATUSES, StudySession, Task
from peace.planner import PlanPrefs, assessment_targets, free_slots, ranked, recommend, remaining_minutes


def valid_allocation(row: StudySession, now: datetime) -> bool:
    if row.event_id:
        return bool(row.event and row.event.kind == 'exam' and row.ends_at <= row.event.starts_at)
    if row.task and row.task.due_at and row.task.due_at > now:
        return row.ends_at <= row.task.due_at
    return True  # overdue tasks still need recovery work


def targets(session: Session, now: datetime, prefs: PlanPrefs):
    tasks = list(session.scalars(select(Task).where(
        Task.status.in_(PLANNABLE_STATUSES), or_(Task.snoozed_until.is_(None), Task.snoozed_until <= now))))
    events = list(session.scalars(select(Event).where(
        Event.kind == 'exam', Event.starts_at > now, Event.starts_at <= now + timedelta(days=30))))
    completed: dict[int, int] = {}
    for row in session.scalars(select(StudySession).where(StudySession.event_id.is_not(None), StudySession.status == 'done')):
        completed[row.event_id] = completed.get(row.event_id, 0) + int((row.ends_at - row.starts_at).total_seconds() // 60)
    return [*tasks, *assessment_targets(events, completed, prefs)]


def materials(index: knowledge.Index, target):
    if getattr(target, 'event_id', None):
        # Without a course association, an assessment title cannot identify trustworthy files.
        return knowledge.suggest_text(index, target.title, target.course_id, 'exam') if target.course_id else []
    return knowledge.suggest(index, target)


def recommendation(session: Session, now: datetime, prefs: PlanPrefs, tz: ZoneInfo):
    candidates = targets(session, now, prefs)
    rec = recommend(candidates, now, prefs)
    if not rec:
        return None
    target = ranked(candidates, now, prefs)[0][0]
    rec['materials'] = materials(knowledge.load_index(session), target)
    rec['course'] = {'id': target.course.id, 'name': target.course.name, 'code': target.course.code} if target.course else None
    rec['due_at'] = target.due_at
    rec['proposed_changes'] = []
    rec['priority'] = 'critical' if target.priority == 4 else 'high' if target.due_at and target.due_at <= now + timedelta(days=3) else 'normal'
    end = min(now + timedelta(days=7), target.due_at) if target.due_at and target.due_at > now else now + timedelta(days=7)
    events = list(session.scalars(select(Event).where(Event.starts_at < end, Event.ends_at > now)))
    today_start = datetime.combine(now.astimezone(tz).date(), time(0), tz)
    sessions = list(session.scalars(select(StudySession).where(
        StudySession.starts_at < end, StudySession.ends_at > today_start, StudySession.status != 'skipped')))
    stale = list(session.scalars(select(StudySession).where(
        or_(StudySession.event_id.is_not(None), StudySession.task_id.is_not(None)),
        StudySession.status == 'planned', StudySession.ends_at > now)))
    rec['proposed_changes'] = [{'session_id': s.id, 'reason': 'Sessão fora do prazo atual; rever horário.'}
                               for s in stale if not valid_allocation(s, now)]
    scheduled = [s for s in sessions if s.status == 'planned' and s.ends_at - max(now, s.starts_at) >= timedelta(minutes=15) and valid_allocation(s, now)
                 and (s.event_id == rec['event_id'] if rec['event_id'] is not None else s.task_id == rec['task_id'])]
    rec['scheduled_minutes'] = sum(int((s.ends_at - max(now, s.starts_at)).total_seconds() // 60) for s in scheduled)
    busy = [(e.starts_at, e.ends_at) for e in events] + [(s.starts_at, s.ends_at) for s in sessions]
    available = 0
    first_slot = None
    day = now.astimezone(tz).date()
    while day <= end.astimezone(tz).date():
        booked = sum(int((s.ends_at - s.starts_at).total_seconds() // 60)
                     for s in sessions if s.starts_at.astimezone(tz).date() == day
                     and (s.status == 'done' or s.ends_at > now))
        target_booked = sum(int((s.ends_at - s.starts_at).total_seconds() // 60)
                            for s in sessions if s.starts_at.astimezone(tz).date() == day
                            and (s.status == 'done' or s.ends_at > now)
                            and (s.event_id == rec['event_id'] if rec['event_id'] is not None else s.task_id == rec['task_id']))
        budget = max(0, min(prefs.max_daily_minutes - booked, prefs.max_task_minutes_per_day - target_booked))
        slots = [(start, min(stop, end)) for start, stop in free_slots(day, tz, prefs, busy, now) if start < end]
        for start, stop in slots:
            slot_minutes = int((stop - start).total_seconds() // 60)
            while slot_minutes >= 15 and budget >= 15:
                available_block = min(budget, slot_minutes)
                length = available_block if available_block - prefs.block_minutes < 15 else prefs.block_minutes
                if first_slot is None:
                    first_slot = start
                    rec['minutes'] = min(rec['minutes'], length)
                available += length
                budget -= length
                slot_minutes -= length + prefs.break_minutes
        day += timedelta(days=1)
    if scheduled:
        existing = min(scheduled, key=lambda s: s.starts_at)
        first_slot = max(now, existing.starts_at)
        rec['minutes'] = min(prefs.block_minutes, int((existing.ends_at - first_slot).total_seconds() // 60))
    rec['starts_at'] = first_slot
    rec['available_minutes'] = available
    rec['warnings'] = []
    if available + rec['scheduled_minutes'] < remaining_minutes(target, prefs):
        rec['warnings'].append('Tempo livre insuficiente para o trabalho restante ou orçamento de preparação; rever o plano.')
    if first_slot is None:
        rec['warnings'].append('Sem bloco livre de pelo menos 15 minutos; não foi alterado nenhum compromisso.')
    if rec['proposed_changes']:
        rec['warnings'].append('Há sessões que precisam de revisão após alterações de prazos ou avaliações; nenhum compromisso foi movido.')
    why = f" ({', '.join(rec['reasons'])})" if rec['reasons'] else ''
    rec['text'] = f"Recomendado: {rec['minutes']} minutos em «{rec['title']}»{why}."
    return rec

"""A study plan for one exam: what to study, for how long, when, and with which files.

The schedule is computed, never written by a model, and says what it rests on. Topics come from the best evidence there
is: the exam's study guide (read from the course's files), else the course's concepts, else the topics of its files,
else a stated default. Sessions are ordinary study sessions, so the week view, the calendar, Google and the timer see
them, and a plan adapts by planning what is left again. Models only feed the guide, in the worker: a plan made before
the guide exists is replanned when it arrives.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from peace import knowledge, mastery, routes
from peace.auth import require_token
from peace.db import get_session
from peace.models import PLANNABLE_STATUSES, Change, Concept, ConceptAnswer, Course, Event, Material, StudyGuide, StudyPlan, StudySession, Task
from peace.planner import MIN_BLOCK, PlanPrefs, free_slots, remaining_minutes
from peace.when import fold, parse_when

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
DB = Depends(get_session)
NOW = Depends(routes.get_now)

RECALL_MINUTES = 20
PRACTICE_SHARE = 0.25  # a quarter of the study time spent on exercises and past exams
TAIL_SHARE = 0.4  # with little time, learning keeps at least the rest
DEFAULT_TOPIC_MINUTES = 60
FILE_TOPIC_MINUTES = 45
MAX_TOPICS = 12
MASTERY_FACTOR = {"weak": 1.5, "developing": 1.0, "untested": 1.0, "mastered": 0.5}
MASTERY_ORDER = {"weak": 0, "developing": 1, "untested": 2, "mastered": 3}


# --- the schedule: pure ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Placed:
    activity: str  # learn | recall | practice | review
    topic: str
    starts_at: datetime
    ends_at: datetime


class _Calendar:
    """The free study time of each day before the exam."""

    def __init__(self, days: list[date], tz: ZoneInfo, prefs: PlanPrefs, now: datetime, exam_at: datetime, busy, used, reserved):
        self.days, self.prefs = days, prefs
        self.slots, self.budget = {}, {}
        for day in days:
            slots = [(s, min(e, exam_at)) for s, e in free_slots(day, tz, prefs, busy(day), now) if s < exam_at]
            self.slots[day] = [(s, e) for s, e in slots if _length(s, e) >= MIN_BLOCK]
            self.budget[day] = prefs.max_daily_minutes - used(day) - reserved(day)

    def capacity(self) -> int:
        return sum(min(max(0, self.budget[d]), sum(_length(s, e) for s, e in self.slots[d])) for d in self.days)

    def take(self, day: date, minutes: int) -> tuple[datetime, datetime] | None:
        """Up to `minutes` from this day, or None when it has no useful time left."""
        while self.slots[day]:
            start, end = self.slots[day][0]
            length = min(minutes, _length(start, end), self.budget[day])
            if length < MIN_BLOCK:
                if self.budget[day] < MIN_BLOCK:
                    return None
                self.slots[day].pop(0)
                continue
            stop = start + timedelta(minutes=length)
            after = stop + timedelta(minutes=self.prefs.break_minutes)
            if _length(after, end) >= MIN_BLOCK:
                self.slots[day][0] = (after, end)
            else:
                self.slots[day].pop(0)
            self.budget[day] -= length
            return start, stop
        return None


def _length(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def needed(topics: list[dict[str, Any]]) -> dict[str, int]:
    """Minutes the plan asks for, per activity."""
    learn = sum(t["minutes"] for t in topics)
    return {"learn": learn, "recall": RECALL_MINUTES * sum(1 for t in topics if t["minutes"] > 0),
            "practice": round(learn * PRACTICE_SHARE)}


def schedule(topics: list[dict[str, Any]], exam_at: datetime, now: datetime, tz: ZoneInfo, prefs: PlanPrefs,
             busy=lambda d: [], used=lambda d: 0, reserved=lambda d: 0) -> tuple[list[Placed], int]:
    """Sessions for `topics` ({topic, minutes}, most important first) and the minutes the whole plan asks for.

    The end first: a review the day before, practice before that. Then learning from today on, most important first, so
    when time is short it is the least important that does not fit; a short recall the day after each topic is finished.
    """
    today = now.astimezone(tz).date()
    last = exam_at.astimezone(tz).date()
    days = [today + timedelta(days=i) for i in range((last - today).days)] or [today]  # the exam's own day only if it is today
    cal = _Calendar(days, tz, prefs, now, exam_at, busy, used, reserved)
    ask = needed(topics)
    review = prefs.block_minutes
    total = sum(ask.values()) + review
    out: list[Placed] = []

    tail_budget = int(cal.capacity() * TAIL_SHARE)
    review = min(review, tail_budget) if tail_budget >= MIN_BLOCK else 0
    practice = min(ask["practice"], tail_budget - review)
    cal.budget[days[-1]] -= review  # kept for the review, which goes after that day's practice
    for day in reversed(days):
        while practice >= MIN_BLOCK and (slot := cal.take(day, practice if practice - prefs.block_minutes < MIN_BLOCK else prefs.block_minutes)):
            out.append(Placed("practice", "", *slot))
            practice -= _length(*slot)
    cal.budget[days[-1]] += review
    if review and (slot := cal.take(days[-1], review)):
        out.append(Placed("review", "", *slot))

    queue = [dict(t) for t in topics if t["minutes"] >= MIN_BLOCK]
    recalls: list[tuple[date, str]] = []
    for day in days:
        for due, topic in [r for r in recalls if r[0] <= day]:
            if slot := cal.take(day, RECALL_MINUTES):
                out.append(Placed("recall", topic, *slot))
                recalls.remove((due, topic))
        while queue:
            t = queue[0]
            left = t["minutes"]
            length = left if left - prefs.block_minutes < MIN_BLOCK else prefs.block_minutes
            slot = cal.take(day, length)
            if not slot:
                break
            out.append(Placed("learn", t["topic"], *slot))
            t["minutes"] -= _length(*slot)
            if t["minutes"] < MIN_BLOCK:
                queue.pop(0)
                recalls.append((day + timedelta(days=1), t["topic"]))
    return sorted(out, key=lambda p: p.starts_at), total


# --- the evidence ---------------------------------------------------------------------------------------------------


def _mastery_of(session: Session, course_id: int | None, now: datetime) -> dict[str, str]:
    if course_id is None:
        return {}
    out = {}
    for c in session.scalars(select(Concept).where(Concept.course_id == course_id)):
        answers = list(session.scalars(select(ConceptAnswer).where(ConceptAnswer.concept_id == c.id)))
        out[fold(c.name)] = mastery.estimate(answers, now)["state"]
    return out


def _state(topic: str, known: dict[str, str]) -> str:
    plain = fold(topic)
    for name, state in known.items():
        if name in plain or plain in name:
            return state
    return "untested"


def evidence(session: Session, exam: Event, now: datetime, prefs: PlanPrefs) -> tuple[list[dict[str, Any]], str, list[str]]:
    """What to study ({topic, minutes, files, state}, most important first), where that came from, and what is assumed."""
    known = _mastery_of(session, exam.course_id, now)
    notes: list[str] = []
    guide = session.scalar(select(StudyGuide).where(StudyGuide.event_id == exam.id))
    if guide and guide.topics:
        topics, basis = [{"topic": t["topic"], "minutes": t["minutes"], "files": t.get("files", [])} for t in guide.topics], "guide"
        notes.append("Os temas e tempos vêm do guia lido dos ficheiros da cadeira: são uma estimativa, não uma medida.")
    else:
        concepts = list(session.scalars(select(Concept).where(Concept.course_id == exam.course_id).order_by(Concept.id))) if exam.course_id else []
        files = list(session.scalars(select(Material).where(Material.course_id == exam.course_id, Material.kind == "file").order_by(Material.modified_at.desc().nulls_last()))) if exam.course_id else []
        if concepts:
            topics, basis = [{"topic": c.name, "minutes": DEFAULT_TOPIC_MINUTES, "files": []} for c in concepts], "concepts"
            notes.append(f"Ainda sem guia desta avaliação: usei os conceitos da cadeira, com {DEFAULT_TOPIC_MINUTES} min por conceito (valor por defeito).")
        elif names := list(dict.fromkeys(t for m in files if m.doc_kind not in ("admin", "statement") for t in (m.topics or []))):
            topics, basis = [{"topic": t, "minutes": FILE_TOPIC_MINUTES, "files": []} for t in names[:MAX_TOPICS]], "files"
            notes.append(f"Ainda sem guia desta avaliação: usei os temas dos ficheiros, com {FILE_TOPIC_MINUTES} min por tema (valor por defeito).")
        elif readable := [m for m in files if m.doc_kind not in ("admin", "statement")]:
            topics, basis = [{"topic": m.title[:200], "minutes": FILE_TOPIC_MINUTES, "files": []} for m in readable[:MAX_TOPICS]], "files"
            notes.append(f"Os ficheiros ainda não foram analisados: dividi o estudo por ficheiro, com {FILE_TOPIC_MINUTES} min cada (valor por defeito).")
        else:
            topics, basis = [{"topic": "Rever a matéria", "minutes": prefs.default_exam_minutes, "files": []}], "none"
            notes.append(f"Não encontrei materiais desta cadeira: reservei {prefs.default_exam_minutes} min no total (valor por defeito). Diz-me os temas, ou sincroniza o Moodle, para um plano melhor.")
    for t in topics:
        t["state"] = _state(t["topic"], known)
        t["minutes"] = round(t["minutes"] * MASTERY_FACTOR[t["state"]])
    if any(t["state"] != "untested" for t in topics):
        notes.append("Os temas onde estás mais fraco vêm primeiro e têm mais tempo; os que dominas têm metade.")
    topics.sort(key=lambda t: MASTERY_ORDER[t["state"]])  # stable: otherwise the guide's or the course's order
    notes.append(f"Até {prefs.max_daily_minutes} min de estudo por dia, entre as {prefs.study_start:%H:%M} e as {prefs.study_end:%H:%M}. Podes mudar isto nas definições.")
    return topics, basis, notes


def _files_for(session: Session, index: Any, exam: Event, activity: str, topic: str, guide_files: dict[str, list]) -> list[dict[str, Any]]:
    if activity == "review":
        return []
    if activity == "practice":
        return knowledge.suggest_text(index, exam.title, exam.course_id, "exam", 2) if exam.course_id else []
    listed = [{"id": f["id"], "title": f["name"], "filename": f["name"], "vault_path": f.get("vault_path"), "url": None, "score": 0.0, "why": "indicado no guia"}
              for f in guide_files.get(topic, [])]
    if listed:
        return listed[:2 if activity == "learn" else 1]
    return knowledge.suggest_text(index, topic, exam.course_id, "study", 2 if activity == "learn" else 1, topic) if exam.course_id else []


def _signature(exam: Event, topics: list[dict[str, Any]], basis: str) -> str:
    # Only what the evidence says: the minutes change as sessions are done, and that is not a reason to replan.
    what = "|".join(t["topic"] for t in topics)
    return hashlib.sha256(f"{exam.starts_at.isoformat()}|{basis}|{what}".encode()).hexdigest()


# --- the plan -------------------------------------------------------------------------------------------------------


def _busy_on(session: Session, tz: ZoneInfo):
    def bounds(day: date) -> tuple[datetime, datetime]:
        return datetime.combine(day, time(0), tz).astimezone(UTC), datetime.combine(day + timedelta(days=1), time(0), tz).astimezone(UTC)

    def busy(day: date) -> list[tuple[datetime, datetime]]:
        start, end = bounds(day)
        events = session.scalars(select(Event).where(Event.starts_at < end, Event.ends_at > start))
        kept = session.scalars(select(StudySession).where(StudySession.starts_at < end, StudySession.ends_at > start, StudySession.status != "skipped"))
        return [(e.starts_at, e.ends_at) for e in events] + [(k.starts_at, k.ends_at) for k in kept]

    def used(day: date) -> int:
        start, end = bounds(day)
        return sum(_length(k.starts_at, k.ends_at) for k in session.scalars(
            select(StudySession).where(StudySession.starts_at < end, StudySession.ends_at > start, StudySession.status != "skipped")))
    return busy, used


def _reserved(session: Session, exam: Event, now: datetime, tz: ZoneInfo, prefs: PlanPrefs, notes: list[str]):
    """Time kept for the work due before the exam, spread evenly until each deadline. Work with its own sessions is
    already counted as busy."""
    tasks = session.scalars(select(Task).where(Task.status.in_(PLANNABLE_STATUSES), Task.due_at > now, Task.due_at <= exam.starts_at))
    per_day: dict[date, int] = {}
    count = 0
    for t in tasks:
        planned = session.scalar(select(func.count()).select_from(StudySession).where(StudySession.task_id == t.id, StudySession.status == "planned"))
        left = remaining_minutes(t, prefs)
        if planned or not left:
            continue
        count += 1
        today = now.astimezone(tz).date()
        span = max(1, (t.due_at.astimezone(tz).date() - today).days)
        for i in range(span):
            d = today + timedelta(days=i)
            per_day[d] = min(prefs.max_daily_minutes // 2, per_day.get(d, 0) + left // span)
    if count:
        notes.append(f"Guardei tempo para {count} {'entrega' if count == 1 else 'entregas'} antes desta avaliação.")
    return lambda day: per_day.get(day, 0)


def replan(session: Session, plan: StudyPlan, now: datetime, missed_before: datetime | None = None) -> None:
    """Plan what is left again. Done sessions stay; missed ones count as skipped; future planned ones are replaced.
    shortcut: a session the student moved by hand is replaced too; pin moved sessions if anyone misses that."""
    session.execute(select(func.pg_advisory_xact_lock(842001)))
    exam = plan.event
    missed_before = missed_before or now
    tz, prefs = routes._tz(), routes._prefs(session)
    rows = list(session.scalars(select(StudySession).where(StudySession.plan_id == plan.id)))
    for row in rows:
        if row.status != "planned":
            continue
        if row.ends_at <= missed_before:
            row.status = "skipped"
        elif row.starts_at >= now:
            session.delete(row)
    session.flush()

    topics, basis, notes = evidence(session, exam, now, prefs)
    done: dict[str, int] = {}
    for row in rows:
        if row.status == "done" and row.activity == "learn" and row.topic:
            done[row.topic] = done.get(row.topic, 0) + _length(row.starts_at, row.ends_at)
    guide_files = {t["topic"]: t["files"] for t in topics}
    remaining = [t | {"minutes": max(0, t["minutes"] - done.get(t["topic"], 0))} for t in topics]
    busy, used = _busy_on(session, tz)
    placed, total = schedule(remaining, exam.starts_at, now, tz, prefs, busy, used, _reserved(session, exam, now, tz, prefs, notes))

    index = knowledge.load_index(session) if placed else None
    new = [StudySession(event_id=exam.id, course_id=exam.course_id, plan_id=plan.id, starts_at=p.starts_at, ends_at=p.ends_at,
                        topic=p.topic[:200] or None, activity=p.activity, reason=f"plano para {exam.title}"[:200],
                        plan_materials=_files_for(session, index, exam, p.activity, p.topic, guide_files))
           for p in placed]
    session.add_all(new)
    plan.topics = [{"topic": t["topic"], "minutes": t["minutes"], "state": t["state"], "done": done.get(t["topic"], 0)} for t in topics]
    plan.basis, plan.assumptions = basis, notes
    plan.needed_minutes, plan.placed_minutes = total, sum(_length(p.starts_at, p.ends_at) for p in placed)
    plan.signature = _signature(exam, topics, basis)
    session.flush()


def create(session: Session, exam: Event, now: datetime) -> StudyPlan:
    """The exam's plan, new or planned again. Sessions the general planner already made for it are replaced."""
    plan = session.scalar(select(StudyPlan).where(StudyPlan.event_id == exam.id))
    if not plan:
        plan = StudyPlan(event_id=exam.id, topics=[], basis="none", needed_minutes=0, placed_minutes=0, signature="")
        session.add(plan)
        session.flush()
        for row in session.scalars(select(StudySession).where(StudySession.event_id == exam.id, StudySession.status == "planned", StudySession.starts_at >= now)):
            session.delete(row)
    replan(session, plan, now)
    return plan


def adapt_all(session: Session, now: datetime) -> int:
    """Worker: replan what changed. A missed session waits until the next day, in case it is marked done afterwards."""
    tz = routes._tz()
    start_of_today = datetime.combine(now.astimezone(tz).date(), time(0), tz).astimezone(UTC)
    changed = 0
    for plan in session.scalars(select(StudyPlan).join(Event, StudyPlan.event_id == Event.id).where(Event.starts_at > now)):
        prefs = routes._prefs(session)
        topics, basis, _ = evidence(session, plan.event, now, prefs)
        rows = list(session.scalars(select(StudySession).where(StudySession.plan_id == plan.id, StudySession.status == "planned")))
        missed = any(r.ends_at <= start_of_today for r in rows)
        clash = any(session.scalar(select(func.count()).select_from(Event).where(Event.starts_at < r.ends_at, Event.ends_at > r.starts_at, Event.id != plan.event_id))
                    for r in rows if r.starts_at >= now)
        if _signature(plan.event, topics, basis) != plan.signature or missed or clash:
            replan(session, plan, now, start_of_today)
            session.add(Change(source="planner", entity="plan", kind="updated", summary=f"Plano de estudo de {plan.event.title} reorganizado."))
            changed += 1
    return changed


def view(session: Session, plan: StudyPlan, now: datetime) -> dict[str, Any]:
    rows = list(session.scalars(select(StudySession).where(StudySession.plan_id == plan.id).order_by(StudySession.starts_at)))
    upcoming = [r for r in rows if r.status == "planned" and r.ends_at > now]
    done = sum(_length(r.starts_at, r.ends_at) for r in rows if r.status == "done")
    planned = sum(_length(r.starts_at, r.ends_at) for r in upcoming)
    short = max(0, plan.needed_minutes - plan.placed_minutes)
    exam = plan.event
    return {
        "id": plan.id,
        "exam": {"id": exam.id, "title": exam.title, "starts_at": exam.starts_at, "course_id": exam.course_id, "course": exam.course.name if exam.course else None},
        "basis": plan.basis,
        "assumptions": plan.assumptions,
        "topics": plan.topics,
        "needed_minutes": plan.needed_minutes,
        "planned_minutes": planned,
        "done_minutes": done,
        "short_minutes": short,
        "enough_time": short < MIN_BLOCK,
        "progress": round(100 * done / (done + planned + short)) if done + planned + short else 100,
        "sessions": routes._with_materials(session, upcoming),
    }


# --- endpoints ------------------------------------------------------------------------------------------------------


class PlanIn(BaseModel):
    event_id: int


def _exam(session: Session, event_id: int, now: datetime) -> Event:
    exam = session.get(Event, event_id)
    if not exam or exam.kind != "exam":
        raise HTTPException(404, "Avaliação não encontrada")
    if exam.starts_at <= now:
        raise HTTPException(422, "Esta avaliação já passou")
    return exam


def _plan(session: Session, plan_id: int) -> StudyPlan:
    plan = session.get(StudyPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Plano não encontrado")
    return plan


@router.get("/study-plans")
def list_plans(session: Session = DB, now: datetime = NOW):
    plans = session.scalars(select(StudyPlan).join(Event, StudyPlan.event_id == Event.id).where(Event.starts_at > now).order_by(Event.starts_at))
    planned = {p.event_id: p for p in plans}
    exams = session.scalars(select(Event).where(Event.kind == "exam", Event.starts_at > now).order_by(Event.starts_at)).all()
    return {"plans": [view(session, p, now) for p in planned.values()],
            "exams": [{"id": e.id, "title": e.title, "starts_at": e.starts_at, "course": e.course.name if e.course else None} for e in exams if e.id not in planned]}


@router.post("/study-plans", status_code=201)
def create_plan(body: PlanIn, session: Session = DB, now: datetime = NOW):
    plan = create(session, _exam(session, body.event_id, now), now)
    session.commit()
    return view(session, plan, now)


@router.post("/study-plans/{plan_id}/replan")
def replan_endpoint(plan_id: int, session: Session = DB, now: datetime = NOW):
    plan = _plan(session, plan_id)
    replan(session, plan, now)
    session.commit()
    return view(session, plan, now)


@router.delete("/study-plans/{plan_id}", status_code=204)
def delete_plan(plan_id: int, session: Session = DB, now: datetime = NOW):
    """Future sessions go; what was studied stays in the record."""
    plan = _plan(session, plan_id)
    for row in session.scalars(select(StudySession).where(StudySession.plan_id == plan.id, StudySession.status == "planned", StudySession.starts_at >= now)):
        session.delete(row)
    session.delete(plan)
    session.commit()


# --- commands -------------------------------------------------------------------------------------------------------

PLAN_WORD = r"(?:plano(?: de estudos?)?|study plan|revision plan|plan)"
CREATE = re.compile(rf"\b(?:cria|criar|faz|fazer|gera|gerar|prepara|preparar|monta|create|make|build|generate)\b.*\b{PLAN_WORD}\b"
                    r"|\b(?:ajuda-me a preparar|ajuda me a preparar|help me prepare|help me study for|prepara-me para)\b")
REPLAN = re.compile(rf"\b(?:reorganiza|reorganizar|refaz|refazer|ajusta|ajustar|replaneia|reorganize|reorganise|rearrange|redo|adjust|replan)\b.*\b{PLAN_WORD}\b"
                    r"|\b(?:perdi|falhei|nao estudei|faltei)\b.*\b(?:ontem|sessao|hoje)\b|\b(?:missed|skipped)\b.*\b(?:yesterday|session|today)\b")
TODAY = re.compile(r"\b(?:o que|que)\b.*\b(?:estudar|estudo)\b.*\bhoje\b|\bwhat\b.*\bstudy\b.*\btoday\b|\bquais?\b.*\b(?:documentos|ficheiros)\b.*\bestudar\b.*\bhoje\b|\bwhich\b.*\bdocuments\b.*\bstudy\b.*\btoday\b")
ENOUGH = re.compile(r"\b(?:tenho|terei|chega o)\b.*\btempo\b.*\b(?:suficiente|para|chega)\b|\bda tempo\b|\b(?:do|will) i have enough time\b|\benough time\b")
HAVE_EXAM = re.compile(r"\b(?:tenho|i have|i've got)\b.*\b(?:exame|teste|frequencia|exam|test|midterm)\b")
EXAM_WORDS = re.compile(r"\b(?:exame|exames|teste|testes|frequencia|avaliacao|exam|exams|test|midterm|plano|plan|study|estudo|estudos|de|do|da|para|for|my|meu|minha|o|a|the|cria|criar|create|make|faz|um|uma|an|tenho|i|have|help|me|prepare|ajuda|preparar|enough|time|tempo|suficiente|peace|proxima|proximo|next|sexta|friday|hoje|amanha|today|tomorrow|segunda|terca|quarta|quinta|sabado|domingo|monday|tuesday|wednesday|thursday|saturday|sunday|on|na|no|preparing|mim)\b")
EN_DAYS = {"tomorrow": "amanha", "today": "hoje", "monday": "segunda", "tuesday": "terca", "wednesday": "quarta", "thursday": "quinta",
           "friday": "sexta", "saturday": "sabado", "sunday": "domingo"}
EXAM_LENGTH = timedelta(hours=2)


def parse(text: str) -> str | None:
    """Which study-plan command this message is, if any. `today` also needs an active plan (see `run`)."""
    plain = fold(text).strip(" ?!.")
    plain = re.sub(r"^(?:ola )?peace[, ]*", "", plain)
    if REPLAN.search(plain):
        return "replan"
    if ENOUGH.search(plain):
        return "enough"
    if CREATE.search(plain) or (HAVE_EXAM.search(plain) and re.search(r"\b(?:ajuda|help|prepar)", plain)):
        return "create"
    if TODAY.search(plain):
        return "today"
    return None


def _hours(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"


def _find_exam(session: Session, text: str, now: datetime, find_course) -> tuple[Event | None, list[Event], Course | None]:
    """(the exam meant, the options when unclear, the course named)."""
    plain = fold(text)
    words = " ".join(EXAM_WORDS.sub(" ", re.sub(r"[^\w ]+", " ", plain)).split())
    course = find_course(session, words) if words else None
    upcoming = list(session.scalars(select(Event).where(Event.kind == "exam", Event.starts_at > now).order_by(Event.starts_at)))
    options = [e for e in upcoming if not course or e.course_id == course.id]
    if course and not options:
        return None, [], course
    if options and (len(options) == 1 or course or re.search(r"\b(?:proxim[oa]|next)\b", plain)):
        return options[0], options, course
    return None, options, course


def _when_said(text: str, now: datetime) -> datetime | None:
    plain = re.sub(r"\bnext\s+", "", fold(text))
    for en, pt in EN_DAYS.items():
        plain = re.sub(rf"\b{en}\b", pt, plain)
    return parse_when(plain, now, routes._tz(), need_day=True)[0]


def run(session: Session, action: str, text: str, now: datetime, find_course) -> tuple[str, list[dict[str, Any]], str] | None:
    """(reply, files, page), or None when this should fall through to PEACE's other answers. Only reports what was done."""
    active = list(session.scalars(select(StudyPlan).join(Event, StudyPlan.event_id == Event.id).where(Event.starts_at > now).order_by(Event.starts_at)))
    if action == "today":
        if not active:
            return None
        rows = list(session.scalars(select(StudySession).where(StudySession.plan_id.in_([p.id for p in active]), StudySession.status == "planned", StudySession.ends_at > now).order_by(StudySession.starts_at).limit(1)))
        if not rows:
            return "O teu plano não tem mais sessões por agora.", [], "/estudo"
        return _describe(rows[0]) + ".", rows[0].plan_materials or [], "/estudo"
    if action == "replan":
        if not active:
            return "Ainda não tens nenhum plano de estudo. Diz, por exemplo, «cria um plano de estudo para o exame de BD».", [], "/estudo"
        for plan in active:
            replan(session, plan, now)
        session.commit()
        first = view(session, active[0], now)
        return f"Reorganizei {_plan_names(active)} com o tempo que resta. {_verdict(first)}", [], "/estudo"

    exam, options, course = _find_exam(session, text, now, find_course)
    when_ = _when_said(text, now)
    if not exam and action == "create" and course and when_ and when_ > now:
        exam = Event(title=f"Exame de {course.name}"[:200], kind="exam", course_id=course.id, starts_at=when_, ends_at=when_ + EXAM_LENGTH)
        session.add(exam)
        session.flush()
    if not exam:
        if options:
            listing = ", ".join(f"{e.title} ({e.starts_at.astimezone(routes._tz()):%d/%m})" for e in options[:6])
            return f"Para qual avaliação? Tens {listing}.", [], "/estudo"
        if course:
            return f"Não conheço nenhuma avaliação de {course.name}. Diz-me a data, por exemplo «tenho exame de {course.name} na sexta, ajuda-me a preparar».", [], "/estudo"
        return "Não encontrei essa avaliação. Diz-me a cadeira e a data, por exemplo «tenho exame de BD na sexta, ajuda-me a preparar».", [], "/estudo"

    if action == "enough":
        plan = session.scalar(select(StudyPlan).where(StudyPlan.event_id == exam.id))
        if plan:
            return _verdict(view(session, plan, now)), [], "/estudo"
        attempt = session.begin_nested()  # the only honest answer comes from trying to place it
        answer = _verdict(view(session, create(session, exam, now), now))
        attempt.rollback()
        return answer + " Ainda não criei o plano: pede-mo se quiseres.", [], "/estudo"

    plan = create(session, exam, now)
    session.commit()
    v = view(session, plan, now)
    next_one = session.scalar(select(StudySession).where(StudySession.plan_id == plan.id, StudySession.status == "planned", StudySession.ends_at > now).order_by(StudySession.starts_at).limit(1))
    start = f" {_describe(next_one)}." if next_one else ""
    return f"Criei o plano para {exam.title}: {len(v['sessions'])} sessões, {_hours(v['planned_minutes'])}. {_verdict(v)}{start}", (next_one.plan_materials or []) if next_one else [], "/estudo"


def _plan_names(plans: list[StudyPlan]) -> str:
    return "o plano de " + ", ".join(p.event.title for p in plans)


ACTIVITY = {"learn": "Estudar", "recall": "Testar de memória", "practice": "Resolver exercícios", "review": "Revisão final"}


def _describe(row: StudySession) -> str:
    at = row.starts_at.astimezone(routes._tz())
    what = ACTIVITY.get(row.activity or "", "Estudar") + (f": {row.topic}" if row.topic else "")
    return f"A seguir: {what}, {at:%d/%m} às {at:%H:%M}"


def _verdict(v: dict[str, Any]) -> str:
    if v["enough_time"]:
        return f"Tens tempo suficiente: precisas de cerca de {_hours(v['needed_minutes'])} e couberam todas."
    return f"Não há tempo para tudo: faltam cerca de {_hours(v['short_minutes'])}, por isso ficou de fora o menos importante."

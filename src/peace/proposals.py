"""Proposals (ADR-020, ADR-029): changes PEACE works out, applied on its own unless the student turned that off.
Deterministic, no LLM.

`replan` (phase 1): when a deadline or an assessment appears or moves, drop that item's future sessions that no
longer fit and plan it again up to its date, around every other commitment.
`recover_*` (phase 3): when planned sessions were missed or open work no longer fits before its date, offer three
alternatives: spread it in the usual study window, stretch the window up to the student's recovery limits, or leave
the lowest-ranked work out (saying what is lost). Deadlines are never touched.
The preview runs the real action inside a savepoint and rolls it back, so what the student sees is exactly what
applying does.
"""

import imaplib
import logging
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from peace import academic, mail, mail_extract, mastery, planner, requirements
from peace.auth import require_token
from peace.config import get_settings
from peace.db import get_session
from peace.models import PLANNABLE_STATUSES, AuditEvent, Change, Event, MailMessage, Preference, Proposal, StudySession, Task
from peace.notifications import _add, _when, planner_prefs
from peace.routes import get_now

HORIZON = timedelta(days=30)  # the planner only prepares assessments this far ahead
PLAN_DAYS = 14  # a re-plan fills at most two weeks, like "planeia a semana" twice
MAIL_FACTS = ("mail_date", "mail_new")  # phase 4b: a date taken from a university email
# Never automatic: one moves mail in the real mailbox, the other leaves the student's work out of the plan.
ASK_FIRST = ("mail_cleanup", "recover_drop")

log = logging.getLogger(__name__)


class ReviewRules(BaseModel):
    """Which detected changes become a Proposal to re-plan (Definições → Rever)."""

    model_config = ConfigDict(extra="forbid")
    auto_apply: bool = True  # PEACE applies what it works out; off means the student approves everything
    deadline_earlier: bool = True
    deadline_later: bool = False
    assessment_new: bool = True
    assessment_moved: bool = True
    assignment_new: bool = True


class Stale(Exception):
    """The proposal no longer applies; the message says why, in pt-PT."""


def rules(session: Session) -> ReviewRules:
    row = session.get(Preference, "review")
    return ReviewRules.model_validate(row.value) if row else ReviewRules()


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return parsed if parsed and parsed.tzinfo else None


def _span(delta: timedelta) -> str:
    days = round(abs(delta).total_seconds() / 86400)
    return f"{days} dia{'s' if days != 1 else ''}" if days else f"{round(abs(delta).total_seconds() / 3600)} h"


def classify(session: Session, change: Change, now: datetime) -> tuple[str, str, Task | None, Event | None] | None:
    """The rule a change falls under, a reason the student can read, and the item to re-plan; None if none applies."""
    tz = _tz()
    detail = change.detail or {}
    if change.entity == "task" and change.entity_id:
        task = session.get(Task, change.entity_id)
        if not task or task.status not in PLANNABLE_STATUSES or not task.due_at or task.due_at <= now:
            return None
        before = _parse(detail.get("due_at", [None])[0]) if change.kind == "updated" else None
        if change.kind == "created" or (change.kind == "updated" and "due_at" in detail and before is None):
            if task.due_at > now + HORIZON:
                return None
            return "assignment_new", f"Trabalho novo, prazo {_when(task.due_at, now, tz)}.", task, None
        if before is None or before == task.due_at:
            return None
        rule, word = ("deadline_earlier", "antecipado") if task.due_at < before else ("deadline_later", "adiado")
        return rule, f"Prazo {word} {_span(task.due_at - before)}: agora {_when(task.due_at, now, tz)}.", task, None
    if change.entity == "event" and change.entity_id:
        event = session.get(Event, change.entity_id)
        if not event or event.kind != "exam" or event.cancelled or not now < event.starts_at <= now + HORIZON:
            return None
        if change.kind == "created":
            return "assessment_new", f"Avaliação nova {_when(event.starts_at, now, tz)}.", None, event
        before = _parse(detail.get("starts_at", [None])[0])
        if change.kind == "updated" and before and before != event.starts_at:
            word = "antecipada" if event.starts_at < before else "adiada"
            return ("assessment_moved", f"Avaliação {word} {_span(event.starts_at - before)}: agora "
                    f"{_when(event.starts_at, now, tz)}.", None, event)
    return None


def _snap(row: StudySession) -> dict[str, Any]:
    item = row.task or row.event
    return {"id": row.id, "task_id": row.task_id, "event_id": row.event_id, "course_id": row.course_id,
            "starts_at": row.starts_at.isoformat(), "ends_at": row.ends_at.isoformat(), "reason": row.reason,
            "title": item.title if item else None}


def _replan(session: Session, task: Task | None, event: Event | None, now: datetime):
    """Drop the item's future sessions that no longer fit, then plan it up to its date. Not committed."""
    if task is None and event is None:
        raise Stale("O item já não existe.")
    if task is not None and task.status not in PLANNABLE_STATUSES:
        raise Stale("A tarefa já está fechada.")
    if event is not None and event.cancelled:
        raise Stale("A avaliação foi cancelada.")
    due = task.due_at if task is not None else event.starts_at
    if due is None or due <= now:
        raise Stale("A data já passou.")
    session.execute(select(func.pg_advisory_xact_lock(842001)))
    mine = StudySession.task_id == task.id if task is not None else StudySession.event_id == event.id
    removed = [r for r in session.scalars(select(StudySession).where(
        mine, StudySession.status == "planned", StudySession.ends_at > now).order_by(StudySession.starts_at))
        if not academic.valid_allocation(r, now)]
    snaps = [_snap(r) for r in removed]
    for r in removed:
        session.delete(r)
    session.flush()
    tz = _tz()
    today = now.astimezone(tz).date()
    last = min(due.astimezone(tz).date(), today + timedelta(days=PLAN_DAYS - 1))
    days = [today + timedelta(days=i) for i in range((last - today).days + 1)]
    created = academic.plan_sessions(session, days, now, tz, planner_prefs(session),
                                     only={task.id if task is not None else -event.id})
    return {"removed": snaps, "created": [_snap(r) for r in created]}


# --- Recovery (phase 3) --------------------------------------------------------------------------

RECOVER = ("recover_spread", "recover_extend", "recover_drop")
MISSED_SINCE = timedelta(days=7)
MAX_DROPPED = 5  # shortcut: tries leaving out up to 5 items; past that the plan needs the student, not PEACE


def _days(now: datetime, tz: ZoneInfo):
    today = now.astimezone(tz).date()
    return [today + timedelta(days=i) for i in range(PLAN_DAYS)]


def _short(session: Session, now: datetime, prefs, ids) -> list[dict[str, Any]]:
    """Targets among `ids` whose remaining work is not covered by future sessions, as plain data."""
    have = academic.allocated(session, now)
    out = []
    for t in academic.targets(session, now, prefs):
        need = planner.remaining_minutes(t, prefs) - have.get(t.id, 0)
        if t.id in ids and need >= planner.MIN_BLOCK:
            out.append({"id": t.id, "title": t.title, "minutes": need})
    return out


def _trial(session: Session, run):
    """Run `run()` in a savepoint and roll it back; returns what it returned."""
    nested = session.begin_nested()
    try:
        return run()
    finally:
        nested.rollback()


def _situation(session: Session, now: datetime, prefs, tz: ZoneInfo) -> tuple[set[int], str]:
    """Targets a recovery plan covers (work missed in the last 7 days and not back in the plan; and, when some work
    would not fit before its date only because other work takes the time, all work due in the plan) and a readable
    reason. Work that would not fit even alone with longer days is named, but planning everything cannot help it."""
    rows = session.scalars(select(StudySession).where(
        StudySession.status == "planned", StudySession.ends_at <= now, StudySession.ends_at > now - MISSED_SINCE)).all()
    missed = {-r.event_id if r.event_id else r.task_id for r in rows} - {None}
    late = {s["id"] for s in _short(session, now, prefs, missed)}
    days = _days(now, tz)
    horizon = datetime.combine(days[-1] + timedelta(days=1), time(0), tz)
    due = {t.id for t in academic.targets(session, now, prefs) if t.due_at and now < t.due_at <= horizon}

    def plan_all():
        academic.plan_sessions(session, days, now, tz, prefs, only=due)
        return _short(session, now, prefs, due)
    infeasible = _trial(session, plan_all)

    def alone(target_id):
        academic.plan_sessions(session, days, now, tz, extended(prefs), only={target_id})
        return _short(session, now, prefs, {target_id})
    hopeless = {s["id"] for s in infeasible if _trial(session, lambda: alone(s["id"]))}
    parts = []
    if late:
        lost = [r for r in rows if (-r.event_id if r.event_id else r.task_id) in late]
        minutes = sum(int((r.ends_at - r.starts_at).total_seconds() // 60) for r in lost)
        parts.append(f"Ficaram por fazer {len(lost)} {'sessão' if len(lost) == 1 else 'sessões'} "
                     f"({planner._plural(minutes, 'minuto', 'minutos')}) que ainda não voltaram ao plano.")
    for s in infeasible:
        hint = ", nem com dias mais longos" if s["id"] in hopeless else ""
        parts.append(f"«{s['title']}» não cabe antes da data{hint}: faltam {s['minutes']} min.")
    # Work squeezed out by other work only fits again in a plan that also holds everything else due.
    squeezed = any(s["id"] not in hopeless for s in infeasible)
    return late | (due if squeezed else set()), " ".join(parts)


def extended(prefs):
    return prefs.model_copy(update={"study_end": max(prefs.study_end, prefs.recovery_study_end),
                                    "max_daily_minutes": max(prefs.max_daily_minutes, prefs.recovery_max_daily_minutes)})


def _recover(session: Session, kind: str, now: datetime) -> dict[str, Any]:
    """One recovery option, for real (not committed). Deadlines and done work are never touched."""
    session.execute(select(func.pg_advisory_xact_lock(842001)))
    tz, prefs = _tz(), planner_prefs(session)
    ids, _ = _situation(session, now, prefs, tz)
    if not ids:
        raise Stale("Já não há trabalho por recuperar.")
    days = _days(now, tz)

    def leave_out(dropped: list[Any]) -> list[dict[str, Any]]:
        out = [t.id for t in dropped]
        rows = session.scalars(select(StudySession).where(
            StudySession.status == "planned", StudySession.ends_at > now,
            StudySession.task_id.in_([i for i in out if i > 0]) | StudySession.event_id.in_([-i for i in out if i < 0]))
            .order_by(StudySession.starts_at)).all()
        snaps = [_snap(r) for r in rows]
        for r in rows:
            session.delete(r)
        session.flush()
        return snaps

    removed, dropped = [], []
    if kind == "recover_drop":
        targets = academic.targets(session, now, prefs)
        horizon = datetime.combine(days[-1] + timedelta(days=1), time(0), tz)
        planned = academic.allocated(session, now)
        # Only work that competes for the same days can free time: due in the plan, or already holding sessions.
        candidates = [t for t, _, _ in reversed(planner.ranked(targets, now, prefs))
                      if (t.due_at and t.due_at <= horizon) or planned.get(t.id)][:MAX_DROPPED]
        for n in range(1, len(candidates) + 1):
            out = candidates[:n]
            keep = ids - {t.id for t in out}
            if not keep:
                break

            def attempt():
                leave_out(out)
                academic.plan_sessions(session, days, now, tz, prefs, only=keep)
                return _short(session, now, prefs, keep)
            if not _trial(session, attempt):
                dropped = out
                break
        if not dropped:
            raise Stale("Mesmo deixando trabalho de fora, não cabe tudo; ajusta o plano à mão.")
        dropped_info = [{"id": t.id, "title": t.title, "minutes": planner.remaining_minutes(t, prefs)} for t in dropped]
        removed = leave_out(dropped)
        ids = ids - {t.id for t in dropped}
        dropped = dropped_info
    elif kind == "recover_extend":
        prefs = extended(prefs)
    created = academic.plan_sessions(session, days, now, tz, prefs, only=ids) if ids else []
    if not removed and not created:
        raise Stale("Não há tempo livre para recuperar nada.")
    return {"removed": removed, "created": [_snap(r) for r in created], "dropped": dropped,
            "short": _short(session, now, planner_prefs(session), ids)}


def _run(session: Session, kind: str, task: Task | None, event: Event | None, now: datetime) -> dict[str, Any]:
    return _recover(session, kind, now) if kind in RECOVER else _replan(session, task, event, now)


def preview(session: Session, kind: str, task: Task | None, event: Event | None, now: datetime) -> dict[str, Any]:
    """What applying would do right now: the real action in a savepoint, rolled back."""
    try:
        return {**_trial(session, lambda: _run(session, kind, task, event, now)), "error": None}
    except Stale as e:
        return {"removed": [], "created": [], "error": str(e)}


def offer_recovery(session: Session, now: datetime, force: bool = False) -> tuple[list[Proposal], str]:
    """Offer the recovery options that would change something (morning briefing, or on demand with `force`), and
    what is wrong (also when no option can help, so nothing that does not fit is hidden).

    Pending options are live (their preview is recomputed), so they are kept rather than offered again.
    Without `force`, at most once per local day."""
    tz = _tz()
    today = now.astimezone(tz).date()
    pending = session.scalars(select(Proposal).where(Proposal.kind.in_(RECOVER), Proposal.status == "pending")).all()
    if pending or (not force and session.scalar(select(Proposal.id).where(Proposal.key.like(f"recover:{today}:%")))):
        return list(pending), pending[0].reason if pending else ""
    prefs = planner_prefs(session)
    ids, reason = _situation(session, now, prefs, tz)
    if not ids:
        return [], reason
    shown = {kind: preview(session, kind, None, None, now) for kind in RECOVER}
    gap = {kind: sum(s["minutes"] for s in shown[kind].get("short", [])) for kind in RECOVER}
    spread_ok = not shown["recover_spread"]["error"]
    offer = {
        "recover_spread": spread_ok,
        # Only worth asking for longer days if they recover more than the usual window does.
        "recover_extend": not shown["recover_extend"]["error"]
                          and (not spread_ok or gap["recover_extend"] < gap["recover_spread"]),
        # Only worth losing work if the other options still leave something out.
        "recover_drop": not shown["recover_drop"]["error"] and min(gap["recover_spread"], gap["recover_extend"]) > 0,
    }
    end = extended(prefs)
    titles = {
        "recover_spread": "Recuperar no horário habitual",
        "recover_extend": f"Recuperar estudando até às {end.study_end:%H:%M} (até {end.max_daily_minutes} min por dia)",
        "recover_drop": "Recuperar deixando trabalho de fora",
    }
    made = []
    for kind in RECOVER:
        if offer[kind]:
            key = f"recover:{today}:{kind}:{now:%H%M%S}"
            made.append(Proposal(key=key, kind=kind, title=titles[kind], reason=reason, evidence=[]))
    session.add_all(made)
    session.flush()
    if made:
        session.add(AuditEvent(actor="peace", action="proposal.offer_recovery", detail={"options": [p.kind for p in made]}))
    return made, reason


def from_changes(session: Session, changes: list[Change], now: datetime) -> list[Proposal]:
    """Turn detected changes into Proposals (same transaction as the sync that found them).

    Only when a rule is switched on and re-planning would actually change something. A newer proposal for the
    same item supersedes an older pending one. Keyed by change id, so running it twice adds nothing."""
    session.flush()
    on = rules(session)
    made = []
    for change in changes:
        hit = classify(session, change, now)
        if hit is None or not getattr(on, hit[0]):
            continue
        rule, reason, task, event = hit
        key = f"change:{change.id}"
        if session.scalar(select(Proposal.id).where(Proposal.key == key)):
            continue
        shown = preview(session, "replan", task, event, now)
        if not shown["removed"] and not shown["created"]:
            continue
        mine = Proposal.task_id == task.id if task is not None else Proposal.event_id == event.id
        for old in session.scalars(select(Proposal).where(mine, Proposal.status == "pending")):
            old.status, old.decided_at = "superseded", now
        item = task or event
        evidence = {"source": change.source, "ref": key, "quote": change.summary, "rule": rule}
        field = "due_at" if task is not None else "starts_at"
        if field in (change.detail or {}):
            evidence["before"], evidence["after"] = change.detail[field]
        proposal = Proposal(key=key, kind="replan", title=f"Re-planear «{item.title}»", reason=reason,
                            task_id=task.id if task else None, event_id=event.id if event else None,
                            change_id=change.id, evidence=[evidence])
        session.add(proposal)
        made.append(proposal)
    session.flush()
    return made


def _audit(session: Session, action: str, proposal: Proposal, actor: str = "user", **detail: Any) -> None:
    session.add(AuditEvent(actor=actor, action=action, detail={"id": proposal.id, "title": proposal.title, **detail}))


def _mail(call, *args):
    """Run a mailbox change; its errors become a refusal the student can read."""
    try:
        return call(*args)
    except (mail.MailError, mail.AuthError) as e:
        raise Stale(str(e)) from None
    except (imaplib.IMAP4.error, OSError):
        raise Stale("O servidor de email falhou a meio; tenta outra vez.") from None


def apply(session: Session, proposal: Proposal, now: datetime, keep: frozenset[int] = frozenset(),
          skip: frozenset[int] = frozenset(), actor: str = "user") -> None:
    if proposal.status != "pending":
        raise Stale("Esta sugestão já foi decidida.")
    if proposal.kind == "concepts":  # `skip`: the concepts the student unticked
        proposal.result = mastery.apply_concepts(session, proposal, set(skip))
        proposal.status, proposal.decided_at = "applied", now
        _audit(session, "proposal.apply", proposal, actor=actor, created=len(proposal.result["concepts"]))
        return
    if proposal.kind == "requirements":
        if proposal.task is None:
            raise Stale("A tarefa já não existe.")
        proposal.result = requirements.apply_diff(session, proposal)
        proposal.status, proposal.decided_at = "applied", now
        _audit(session, "proposal.apply", proposal, actor=actor)
        return
    if proposal.kind in MAIL_FACTS:
        try:
            proposal.result = mail_extract.apply_fact(session, proposal, now)
        except mail_extract.Refused as e:
            raise Stale(str(e)) from None
        proposal.status, proposal.decided_at = "applied", now
        _audit(session, "proposal.apply", proposal, actor=actor)
        return
    if proposal.kind == "mail_cleanup":  # `keep`: the messages the student unticked
        proposal.result = _mail(mail.apply_cleanup, session, proposal, set(keep), now)
        proposal.status, proposal.decided_at = "applied", now
        _audit(session, "proposal.apply", proposal, actor=actor, moved=len(proposal.result["moved"]))
        return
    done = _run(session, proposal.kind, proposal.task, proposal.event, now)
    proposal.result = {"removed": done["removed"], "created": done["created"], "dropped": done.get("dropped", [])}
    proposal.status, proposal.decided_at = "applied", now
    if proposal.kind in RECOVER:  # the other options were alternatives to this one
        for other in session.scalars(select(Proposal).where(Proposal.kind.in_(RECOVER), Proposal.status == "pending",
                                                            Proposal.id != proposal.id)):
            other.status, other.decided_at = "superseded", now
    _audit(session, "proposal.apply", proposal, actor=actor, removed=len(done["removed"]), created=len(done["created"]))


def auto_apply(session: Session, now: datetime) -> list[Proposal]:
    """Apply what is waiting, unless the student asked to approve everything. Oldest first, so of the recovery options
    the usual window wins and the others are superseded. What no longer applies stays pending for the student."""
    if not rules(session).auto_apply:
        return []
    done = []
    query = select(Proposal).where(Proposal.status == "pending", Proposal.kind.not_in(ASK_FIRST)).order_by(Proposal.id)
    for proposal in session.scalars(query).all():
        if proposal.status != "pending":  # superseded by the option applied just before
            continue
        try:
            with session.begin_nested():
                apply(session, proposal, now, actor="peace")
        except Stale:
            continue
        except Exception:  # one bad proposal must not stop the others or the worker
            log.exception("could not apply proposal %s", proposal.id)
            continue
        done.append(proposal)
    if done:
        lines = [p.title for p in done[:3]] + ([f"+{len(done) - 3} outras"] if len(done) > 3 else [])
        _add(session, key=f"auto:{done[-1].id}", kind="changes", title="A PEACE atualizou o teu plano", body="\n".join(lines),
             url="/rever", actions=["open"], expires_at=now + timedelta(days=2))
    return done


def reject(session: Session, proposal: Proposal, now: datetime) -> None:
    if proposal.status != "pending":
        raise Stale("Esta sugestão já foi decidida.")
    proposal.status, proposal.decided_at = "rejected", now
    _audit(session, "proposal.reject", proposal)


def undo(session: Session, proposal: Proposal, now: datetime) -> None:
    """Put the sessions back as they were, only if nothing PEACE created has been touched since."""
    if proposal.status != "applied":
        raise Stale("Só se pode desfazer uma sugestão aplicada.")
    if proposal.kind == "concepts":
        mastery.undo_concepts(session, proposal)
        proposal.status, proposal.decided_at = "undone", now
        _audit(session, "proposal.undo", proposal)
        return
    if proposal.kind == "requirements":
        requirements.undo_diff(session, proposal)
        proposal.status, proposal.decided_at = "undone", now
        _audit(session, "proposal.undo", proposal)
        return
    if proposal.kind in MAIL_FACTS:
        try:
            mail_extract.undo_fact(session, proposal)
        except mail_extract.Refused as e:
            raise Stale(str(e)) from None
        proposal.status, proposal.decided_at = "undone", now
        _audit(session, "proposal.undo", proposal)
        return
    if proposal.kind == "mail_cleanup":
        rows = [r for i in proposal.result.get("moved", []) if (r := session.get(MailMessage, i)) and r.removed_at]
        _mail(mail.restore, session, rows)
        proposal.status, proposal.decided_at = "undone", now
        _audit(session, "proposal.undo", proposal)
        return
    session.execute(select(func.pg_advisory_xact_lock(842001)))
    created = proposal.result.get("created", [])
    rows = [session.get(StudySession, c["id"]) for c in created]
    for snap, row in zip(created, rows):
        if (row is None or row.status != "planned" or row.starts_at != datetime.fromisoformat(snap["starts_at"])
                or row.ends_at != datetime.fromisoformat(snap["ends_at"])):
            raise Stale("Uma das sessões criadas já foi mudada, feita ou apagada; ajusta o plano à mão.")
    for row in rows:
        session.delete(row)
    session.add_all(StudySession(task_id=s["task_id"], event_id=s["event_id"], course_id=s["course_id"],
                                 starts_at=datetime.fromisoformat(s["starts_at"]),
                                 ends_at=datetime.fromisoformat(s["ends_at"]), reason=s["reason"])
                    for s in proposal.result.get("removed", []))
    proposal.status, proposal.decided_at = "undone", now
    _audit(session, "proposal.undo", proposal)


# --- API ---------------------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
DB = Depends(get_session)
NOW = Depends(get_now)


def _out(session: Session, p: Proposal, now: datetime) -> dict[str, Any]:
    item = p.task or p.event
    out = {
        "id": p.id, "kind": p.kind, "status": p.status, "title": p.title, "reason": p.reason,
        "created_at": p.created_at, "decided_at": p.decided_at, "evidence": p.evidence,
        "item": item and {"type": "task" if p.task else "exam", "id": item.id, "title": item.title,
                          "course": item.course.name if item.course else None,
                          "at": p.task.due_at if p.task else p.event.starts_at},
    }
    if p.status != "pending":
        out["preview"] = None
    elif p.kind == "concepts":  # a course's proposed concepts, each one can be unticked
        out["preview"] = {"removed": [], "created": [], "concepts": p.evidence[0]["concepts"], "error": None}
    elif p.kind == "requirements":  # a re-read brief: the checklist's difference
        out["preview"] = {"removed": [], "created": [], "diff": p.evidence[0]["diff"],
                          "error": None if p.task else "A tarefa já não existe."}
    elif p.kind in MAIL_FACTS:  # a date to take from an email: nothing to plan until it is applied
        out["preview"] = {"removed": [], "created": [], "error": mail_extract.problem(p, now)}
    elif p.kind == "mail_cleanup":  # nothing to plan: the listed messages that may still go
        still = {r.id for r in mail.candidates(session, now)}
        messages = [e for e in p.evidence if int(e["ref"]) in still]
        out["preview"] = {"removed": [], "created": [], "messages": messages,
                          "error": None if messages else "Já não há nada destes emails para limpar."}
    else:
        out["preview"] = preview(session, p.kind, p.task, p.event, now)
    out["result"] = p.result if p.status in ("applied", "undone") else None
    return out


@router.get("/proposals")
def list_proposals(status: str = "pending", session: Session = DB, now: datetime = NOW):
    """Pending proposals (with a fresh preview), or the decided ones (`status=history`, newest first)."""
    query = select(Proposal)
    query = query.where(Proposal.status == "pending") if status == "pending" else query.where(Proposal.status != "pending")
    rows = session.scalars(query.order_by(Proposal.id.desc()).limit(100)).all()
    return [_out(session, p, now) for p in rows]


def _decide(action, proposal_id: int, session: Session, now: datetime):
    proposal = session.get(Proposal, proposal_id)
    if proposal is None:
        raise HTTPException(404, "Sugestão não encontrada.")
    try:
        action(session, proposal, now)
    except Stale as e:
        session.rollback()
        raise HTTPException(409, str(e)) from None
    session.commit()
    return _out(session, proposal, now)


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keep: list[int] = []  # cleanup: messages to leave where they are
    skip: list[int] = []  # concepts: the positions of the ones not wanted


@router.post("/proposals/{proposal_id}/apply")
def apply_proposal(proposal_id: int, body: ApplyIn | None = None, session: Session = DB, now: datetime = NOW):
    keep, skip = (frozenset(body.keep), frozenset(body.skip)) if body else (frozenset(), frozenset())
    return _decide(lambda s, p, n: apply(s, p, n, keep, skip), proposal_id, session, now)


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, session: Session = DB, now: datetime = NOW):
    return _decide(reject, proposal_id, session, now)


@router.post("/proposals/{proposal_id}/undo")
def undo_proposal(proposal_id: int, session: Session = DB, now: datetime = NOW):
    return _decide(undo, proposal_id, session, now)


@router.post("/recovery")
def recover(session: Session = DB, now: datetime = NOW):
    """Offer recovery options now («recupera a semana»): the pending ones, and what is wrong (empty: nothing)."""
    made, note = offer_recovery(session, now, force=True)
    session.commit()
    return {"options": [_out(session, p, now) for p in made], "note": note}


@router.get("/preferences/review", response_model=ReviewRules)
def get_review_rules(session: Session = DB):
    return rules(session)


@router.put("/preferences/review", response_model=ReviewRules)
def put_review_rules(body: ReviewRules, session: Session = DB):
    value = body.model_dump()
    row = session.get(Preference, "review")
    if row:
        row.value = value
    else:
        session.add(Preference(key="review", value=value))
    session.add(AuditEvent(actor="user", action="preferences.review", detail=value))
    session.commit()
    return body

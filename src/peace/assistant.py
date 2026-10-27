"""The PEACE assistant: understands a request, runs it with PEACE's own planner and data, answers in pt-PT.

The local model (Ollama, gpt-oss) only chooses which built-in command to run and with which arguments.
Facts and priorities always come from the database and the deterministic planner, so the model cannot
invent them (tested: on its own it gave tomorrow's exam 30 minutes). Without the model a keyword
matcher picks the command, and the status says the model is offline.
"""

import json
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from peace import desktop, knowledge, routes
from peace import schemas as s
from peace.auth import require_token
from peace.db import get_session
from peace.models import PLANNABLE_STATUSES, AssistantMessage, Course, Event, Material, Preference, StudySession
from peace.llm import LLM_MODEL, LLM_OPTIONS, LLM_TIMEOUT, _llm, llm_reachable, mark_unreachable
from peace.notifications import WEEKDAYS, focus_until

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
DB = Depends(get_session)
NOW = Depends(routes.get_now)

States = Literal["idle", "listening", "thinking", "speaking"]
STATE_TTL = {"listening": 120, "thinking": 180, "speaking": 300}  # a stuck state falls back to idle
DESKTOP_TTL = 90  # the agent's heartbeat is every 30 s


@dataclass
class Reply:
    text: str
    intent: str
    items: list[dict[str, Any]] = field(default_factory=list)
    to: str | None = None  # a dashboard page that shows more
    job: dict[str, Any] | None = None  # a plan for the desktop agent (see peace.desktop)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _hm(at: datetime) -> str:
    return f"{at.astimezone(routes._tz()):%H:%M}"


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} e {parts[-1]}"


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _day_name(day: date, today: date) -> str:
    days = (day - today).days
    return "hoje" if days == 0 else "amanhã" if days == 1 else f"{WEEKDAYS[day.weekday()]} ({day:%d/%m})"


def _on_day(day: date, today: date) -> str:
    """Sentence opener: "Hoje", "Amanhã", "Na quarta (28/10)", "No sábado (31/10)"."""
    name = _day_name(day, today)
    if (day - today).days < 2:
        return name.capitalize()
    return ("No " if day.weekday() >= 5 else "Na ") + name


def _material_item(m: dict[str, Any]) -> dict[str, Any]:
    return {"title": m["title"], "detail": m["why"], "vault_path": m["vault_path"], "url": m["url"]}


# --- commands (each one is also a tool the model can call) ---------------------------------------


def cmd_day(session: Session, now: datetime, offset: int = 0) -> Reply:
    """Classes, exams, study sessions and deadlines of one day."""
    tz = routes._tz()
    today = now.astimezone(tz).date()
    offset = max(0, min(int(offset), 30))
    day = today + timedelta(days=offset)
    start, end = routes._day_bounds(day, tz)
    events = session.scalars(select(Event).where(Event.starts_at < end, Event.ends_at > start).order_by(Event.starts_at)).all()
    sessions = [x for x in routes._sessions_between(session, start, end) if x.status == "planned"]
    due = [d for d in routes._deadline_items(session, now, offset + 2) if d["type"] != "exam" and start <= d["due_at"] < end]
    overdue = [t for t in routes._visible_open_tasks(session, now) if t.due_at and t.due_at < now] if offset == 0 else []
    label = _on_day(day, today)

    items = [{"title": e.title, "detail": f"{_hm(e.starts_at)}–{_hm(e.ends_at)}" + (f" · {e.location}" if e.location else "")} for e in events]
    items += [{"title": f"Estudo: {x.task.title if x.task else x.event.title if x.event else 'sessão livre'}", "detail": f"{_hm(x.starts_at)}–{_hm(x.ends_at)}"} for x in sessions]
    items += [{"title": f"Entrega: {d['title']}", "detail": _hm(d["due_at"])} for d in due]

    classes = [e for e in events if e.kind == "class"]
    exams = [e for e in events if e.kind == "exam"]
    parts = []
    if classes:
        parts.append(f"{_n(len(classes), 'aula', 'aulas')} ({_join([f'{e.title} às {_hm(e.starts_at)}' for e in classes])})")
    if exams:
        parts.append(f"{_n(len(exams), 'avaliação', 'avaliações')}: {_join([f'{e.title} às {_hm(e.starts_at)}' for e in exams])}")
    if sessions:
        parts.append(_n(len(sessions), "sessão de estudo", "sessões de estudo"))
    text = f"{label} tens {_join(parts)}." if parts else f"{label} não tens aulas, avaliações nem estudo marcado."
    if due:
        text += f" {_n(len(due), 'entrega', 'entregas').capitalize()}: {_join([d['title'] for d in due])}."
    if offset == 0:
        text += f" {_n(len(overdue), 'tarefa', 'tarefas')} em atraso." if overdue else " Nada em atraso."
    return Reply(text, "day", items, "/calendario?date=" + day.isoformat())


def cmd_deadlines(session: Session, now: datetime, days: int = 7) -> Reply:
    """Deadlines and exams in the next N days."""
    days = max(1, min(int(days), 60))
    rows = routes._deadline_items(session, now, days)
    tz = routes._tz()
    today = now.astimezone(tz).date()
    items = [
        {"title": d["title"], "detail": f"{_day_name(d['due_at'].astimezone(tz).date(), today)} às {_hm(d['due_at'])}" + (" · em atraso" if d["overdue"] else "")}
        for d in rows
    ]
    span = "Até amanhã" if days == 1 else f"Nos próximos {days} dias"
    if not rows:
        return Reply(f"{span} não tens prazos nem avaliações.", "deadlines", [], "/prazos")
    exams = sum(d["type"] == "exam" for d in rows)
    text = (f"{span}: {_n(len(rows) - exams, 'prazo', 'prazos')} e {_n(exams, 'avaliação', 'avaliações')}. "
            f"O mais próximo é {rows[0]['title']}, {items[0]['detail']}.")
    return Reply(text, "deadlines", items, "/prazos")


def _sessions_reply(session: Session, rows: list[StudySession], now: datetime, intent: str, empty: str) -> Reply:
    rows = [x for x in rows if x.status == "planned" and x.ends_at > now]
    if not rows:
        return Reply(empty, intent, [], "/estudo")
    index = knowledge.load_index(session)
    today = now.astimezone(routes._tz()).date()
    items = []
    for x in rows:
        files = knowledge.suggest(index, x.task) if x.task else knowledge.suggest_text(index, x.event.title, x.event.course_id, 'exam') if x.event else []
        when = f"{_day_name(x.starts_at.astimezone(routes._tz()).date(), today)} {_hm(x.starts_at)}–{_hm(x.ends_at)}"
        items.append({"title": x.task.title if x.task else x.event.title if x.event else "Sessão livre", "detail": when + (f" · {files[0]['title']}" if files else "")})
    minutes = sum(routes._minutes(x.starts_at, x.ends_at) for x in rows)
    return Reply(f"Planeei {_n(len(rows), 'sessão', 'sessões')}, {minutes // 60} h {minutes % 60:02d} min no total.", intent, items, "/estudo")


def cmd_plan_day(session: Session, now: datetime) -> Reply:
    """Plan study sessions for the rest of today, in the free time between classes."""
    tz = routes._tz()
    day = now.astimezone(tz).date()
    routes._plan(session, [day], now)
    rows = routes._sessions_between(session, *routes._day_bounds(day, tz))
    return _sessions_reply(session, rows, now, "plan_day", "Hoje já não há tempo livre ou tarefas para planear.")


def cmd_plan_week(session: Session, now: datetime) -> Reply:
    """Plan study sessions for the next 7 days."""
    tz = routes._tz()
    days = [now.astimezone(tz).date() + timedelta(days=i) for i in range(7)]
    routes._plan(session, days, now)
    rows = routes._sessions_between(session, routes._day_bounds(days[0], tz)[0], routes._day_bounds(days[-1], tz)[1])
    return _sessions_reply(session, rows, now, "plan_week", "Não há tarefas para planear esta semana.")


def cmd_study_now(session: Session, now: datetime) -> Reply:
    """What to study right now and with which files."""
    rec = routes.get_recommendation(session, now)
    if not rec:
        return Reply("Nada urgente por agora.", "study_now", [], "/estudo")
    files = rec.get("materials") or []
    text = rec["text"] + (f" Usa {_join([f['title'] for f in files])}." if files else "")
    return Reply(text, "study_now", [_material_item(f) for f in files], "/estudo")


def find_course(session: Session, query: str) -> Course | None:
    """Course named in free text: by code, acronym (BD, MOAD) or a significant word of its name."""
    words = set(re.findall(r"[a-z0-9]{2,}", _fold(query)))
    best, score = None, 0
    for c in session.scalars(select(Course).order_by(Course.id)):
        name = re.findall(r"[a-z0-9]+", _fold(c.name))
        acronym = "".join(w[0] for w in name if len(w) > 2)
        points = sum(3 for w in name if len(w) >= 4 and w in words) + (5 if acronym in words or (c.code or "").lower() in words else 0)
        if points > score:
            best, score = c, points
    return best


def cmd_materials(session: Session, now: datetime, course: str = "") -> Reply:
    """Files to study for a course (or for the recommended task when no course is named)."""
    if not course.strip():
        return cmd_study_now(session, now)
    match = find_course(session, course)
    if not match:
        return Reply(f"Não encontrei a cadeira «{course.strip()}».", "materials", [], "/ficheiros")
    return _course_materials(session, now, match)


def _course_materials(session: Session, now: datetime, match: Course) -> Reply:
    exam = session.scalar(select(Event).where(Event.kind == "exam", Event.course_id == match.id, Event.starts_at > now,
                                              Event.starts_at < now + timedelta(days=14)).order_by(Event.starts_at).limit(1))
    if exam:  # an exam is coming: study material for it, not for the next assignment
        files = knowledge.suggest_text(knowledge.load_index(session), exam.title, match.id, "exam")
        text = f"Para {exam.title} ({_day_name(exam.starts_at.astimezone(routes._tz()).date(), now.astimezone(routes._tz()).date())}): {_join([f['title'] for f in files])}." if files else f"Sem material específico para {exam.title}."
        return Reply(text, "materials", [_material_item(f) for f in files], f"/cadeiras/{match.id}")
    task = next((t for t in routes._visible_open_tasks(session, now) if t.course_id == match.id), None)
    if task:
        files = knowledge.suggest(knowledge.load_index(session), task)
        text = f"Para {task.title} ({match.name}): {_join([f['title'] for f in files])}." if files else f"Sem material específico para {task.title}."
        return Reply(text, "materials", [_material_item(f) for f in files], f"/cadeiras/{match.id}")
    recent = session.scalars(select(Material).where(Material.course_id == match.id, Material.kind == "file")
                             .order_by(Material.modified_at.desc().nulls_last()).limit(5)).all()
    items = [{"title": m.title, "detail": "recente", "vault_path": m.vault_path, "url": m.url} for m in recent]
    text = f"{match.name} não tem tarefas abertas; estes são os ficheiros mais recentes." if recent else f"{match.name} ainda não tem ficheiros."
    return Reply(text, "materials", items, f"/cadeiras/{match.id}")


def _next_session(session: Session, now: datetime) -> StudySession | None:
    return session.scalar(select(StudySession).where(StudySession.status == "planned", StudySession.ends_at > now)
                          .order_by(StudySession.starts_at).limit(1))


def cmd_skip_session(session: Session, now: datetime) -> Reply:
    """Skip the next planned study session."""
    x = _next_session(session, now)
    if not x:
        return Reply("Não tens sessões de estudo marcadas.", "skip_session", [], "/estudo")
    x.status = "skipped"
    routes._audit(session, "session.skip", id=x.id, via="assistant")
    session.commit()
    return Reply(f"Saltei a sessão das {_hm(x.starts_at)} ({x.task.title if x.task else x.event.title if x.event else 'sessão livre'}).", "skip_session", [], "/estudo")


def cmd_postpone_session(session: Session, now: datetime) -> Reply:
    """Move the next planned study session to the same time tomorrow."""
    x = _next_session(session, now)
    if not x:
        return Reply("Não tens sessões de estudo marcadas.", "postpone_session", [], "/estudo")
    tz = routes._tz()
    start = x.starts_at.astimezone(tz) + timedelta(days=1)
    end = x.ends_at.astimezone(tz) + timedelta(days=1)
    if x.event and end > x.event.starts_at:
        return Reply('A sessão ficaria depois da avaliação. Escolhe outra hora no plano de estudo.', 'postpone_session', [], '/estudo')
    x.starts_at, x.ends_at = start, end
    routes._audit(session, "session.postpone", id=x.id, via="assistant")
    session.commit()
    return Reply(f"Passei a sessão de {x.task.title if x.task else x.event.title if x.event else 'estudo'} para amanhã às {_hm(x.starts_at)}.", "postpone_session", [], "/estudo")


def set_focus(session: Session, now: datetime, minutes: int | None) -> datetime | None:
    until = now + timedelta(minutes=minutes) if minutes else None
    _put(session, "focus", {"until": until.isoformat() if until else None})
    routes._audit(session, "focus.on" if until else "focus.off", minutes=minutes)
    session.commit()
    return until


def cmd_focus(session: Session, now: datetime, minutes: int = 50) -> Reply:
    """Turn focus mode on: only urgent notifications until it ends."""
    until = set_focus(session, now, max(5, min(int(minutes), 240)))
    return Reply(f"Modo foco ligado até às {_hm(until)}. Só passam avisos urgentes.", "focus")


def cmd_focus_off(session: Session, now: datetime) -> Reply:
    """Turn focus mode off."""
    set_focus(session, now, None)
    return Reply("Modo foco desligado.", "focus_off")


HELP = ("Posso dizer-te o que tens hoje ou amanhã, os prazos da semana, o que estudar agora e com que ficheiros, "
        "planear o dia ou a semana, saltar ou adiar a próxima sessão e ligar o modo foco. No PC posso preparar o estudo "
        "de uma cadeira, abrir aplicações e ficheiros, arrumar janelas e áreas de trabalho e gerir separadores do Brave.")


def cmd_help(session: Session, now: datetime) -> Reply:
    """What the assistant can do (for greetings and requests outside the other commands)."""
    return Reply(HELP, "help")


# A message that is only a greeting or thanks: answered directly. The model must pick a tool and, with
# recent history in view, would read "olá" as the previous question again.
GREETING = re.compile(r"(?:diz[- ]?me |diga |say )?(?:ola|oi|hey|hello|hi|bom dia|boa tarde|boa noite)(?: peace)?")
THANKS = re.compile(r"(?:muito )?(?:obrigad[oa]|obg|valeu)(?: peace)?")


def cmd_greet(session: Session, now: datetime, thanks: bool = False) -> Reply:
    if thanks:
        return Reply("De nada!", "help")
    name = _pref(session, "appearance").get("name", "").strip()
    return Reply(f"Olá{', ' + name if name else ''}! Em que te posso ajudar?", "help")


# --- desktop actions (carried out and verified by the desktop agent; see peace.desktop) ---------------


class _Unknown(dict):
    def __missing__(self, key: str) -> str:
        return "?"


DESCRIBE = {"open_app": "abrir {app}", "find_files": "procurar ficheiros «{query}»", "open_file": "abrir o ficheiro «{query}»",
            "move_file": "mover «{query}» para {dest_root}/{dest_dir}", "make_folder": "criar a pasta {root}/{path}",
            "trash_file": "pôr «{query}» no lixo", "focus_window": "pôr {window} em foco",
            "move_window": "mover {window} para a área {workspace}", "place_window": "arrumar a janela {window}",
            "close_window": "fechar a janela {window}", "switch_workspace": "mudar para a área de trabalho {workspace}",
            "apply_layout": "aplicar a disposição «{name}»", "save_layout": "guardar a disposição «{name}»",
            "undo_layout": "repor as janelas como estavam", "find_tabs": "procurar separadores «{query}»", "open_tab": "abrir {url}",
            "focus_tab": "mostrar o separador «{query}»", "group_tabs": "agrupar os separadores «{query}» em «{title}»",
            "close_tabs": "fechar os separadores «{query}»"}


def desktop_connected(session: Session, now: datetime) -> bool:
    desktop_pref = _pref(session, "desktop")
    seen = datetime.fromisoformat(desktop_pref["seen_at"]) if desktop_pref.get("seen_at") else None
    return bool(seen and now - seen < timedelta(seconds=DESKTOP_TTL))


def run_desktop(session: Session, now: datetime, steps: list[dict[str, Any]], title: str, items: list | None = None) -> Reply:
    """Hand a plan to the desktop agent; the reply says it is under way, the job's results say what happened."""
    if not desktop_connected(session, now):
        return Reply("O agente do PC está desligado, por isso não mexi no ambiente de trabalho.", "desktop", items or [])
    try:
        job = desktop.create_job(session, steps, title, now)
    except HTTPException as e:
        return Reply(str(e.detail), "desktop", items or [])
    if job["state"] == "confirm":
        _put(session, "assistant_pending", {"name": "desktop_confirm", "args": {"job": job["id"]}, "at": now.isoformat()})
        return Reply(f"Confirmas: {title}? Isto pode fechar ou apagar algo. Responde «sim».", "confirm", items or [], job=desktop.public(job))
    return Reply(f"A tratar disso no PC: {title}.", "desktop", items or [], job=desktop.public(job))


def cmd_desktop(session: Session, now: datetime, name: str, args: dict[str, Any]) -> Reply:
    return run_desktop(session, now, [desktop.step(name, args)], DESCRIBE[name].format_map(_Unknown(args)))


def cmd_desktop_plan(session: Session, now: datetime, calls: list[tuple[str, dict[str, Any]]]) -> Reply:
    steps = [desktop.step(n, a) for n, a in calls]
    return run_desktop(session, now, steps, "; ".join(DESCRIBE[n].format_map(_Unknown(a)) for n, a in calls))


def cmd_desktop_confirm(session: Session, now: datetime, job: str = "") -> Reply:
    confirmed = desktop.confirm_job(session, job, now)
    if not confirmed:
        return Reply("Essa ação já não está à espera de confirmação. Pede outra vez.", "desktop")
    return Reply(f"Confirmado. A tratar disso no PC: {confirmed['title']}.", "desktop", job=desktop.public(confirmed))


def cmd_cancel_desktop(session: Session, now: datetime) -> Reply:
    """Cancel the action under way on the PC."""
    job = desktop._live(desktop._pref(session, "desktop_job"), now)
    if job.get("state") not in ("pending", "running", "confirm"):
        return Reply("Não há nenhuma ação no PC em curso.", "cancel_desktop")
    after = desktop.cancel_job(job["id"], session=session, now=now)
    return Reply("Cancelado antes de começar; nada foi feito." if after["state"] == "cancelled" else
                 "Vou parar antes do próximo passo e digo-te o que já ficou feito.", "cancel_desktop", job=after)


def _short(course: Course) -> str:
    words = re.findall(r"[A-Za-zÀ-ÿ0-9]+", course.name)
    acronym = "".join(w[0] for w in words if len(w) > 2).upper()
    return acronym if 2 <= len(acronym) <= 6 else course.name[:30]


def cmd_study_workspace(session: Session, now: datetime, course: str = "") -> Reply:
    """Prepare the PC to study a course: the study layout, its best files and the course page in a tab group."""
    match = find_course(session, course) if course.strip() else None
    if not match:
        return Reply(f"Não encontrei a cadeira «{course.strip()}»." if course.strip() else "Diz-me que cadeira queres estudar.",
                     "study_workspace", [], "/cadeiras")
    files = [i for i in _course_materials(session, now, match).items if i.get("vault_path")][:3]
    if not files:  # nothing matched the next task or exam: the course's latest files, never invented ones
        recent = session.scalars(select(Material).where(Material.course_id == match.id, Material.kind == "file", Material.vault_path.is_not(None))
                                 .order_by(Material.modified_at.desc().nulls_last()).limit(3)).all()
        files = [{"title": m.title, "detail": "recente", "vault_path": m.vault_path, "url": m.url} for m in recent]
    steps = [desktop.step("apply_layout", {"name": "estudo"})]
    steps += [{"tool": "files.open", "args": {"root": "vault", "path": i["vault_path"]}} for i in files]
    title = f"preparar o estudo de {match.name} (disposição «estudo», {len(files)} ficheiro(s)"
    if match.url and re.match(r"https?://", match.url):
        steps.append({"tool": "browser.open", "args": {"url": match.url, "group": _short(match)}})
        title += ", página da cadeira no Brave"
    reply = run_desktop(session, now, steps, title + ")", files)
    reply.intent, reply.to = "study_workspace", f"/cadeiras/{match.id}"
    return reply


TOOL_HELP = {
    "day": "Tudo o que o utilizador tem num dia concreto: aulas, avaliações, sessões de estudo e entregas. "
           "Usar para 'o que tenho hoje', 'o que tenho amanhã', 'e na quarta?', 'plano de hoje'.",
    "deadlines": "Lista de prazos de entrega e avaliações ao longo de vários dias (semana, mês). "
                 "Não usar para perguntas sobre um só dia: para isso usar day.",
    "plan_day": "Planear (criar) sessões de estudo para o resto de hoje.",
    "plan_week": "Planear (criar) sessões de estudo para os próximos 7 dias.",
    "study_now": "O que estudar agora e com que ficheiros.",
    "materials": "Ficheiros a usar para estudar uma cadeira.",
    "skip_session": "Saltar (cancelar) a próxima sessão de estudo.",
    "postpone_session": "Adiar a próxima sessão de estudo para amanhã.",
    "focus": "Ligar o modo foco (só avisos urgentes).",
    "focus_off": "Desligar o modo foco.",
    "study_workspace": "Preparar o PC para estudar uma cadeira: arrumar janelas, abrir os ficheiros certos e a página da cadeira. "
                       "Usar para 'vamos estudar X', 'quero estudar X agora no PC'.",
    "cancel_desktop": "Cancelar a ação que está a decorrer no PC.",
    "help": "Cumprimentos ou pedidos que nenhuma outra ferramenta cobre.",
}

COMMANDS = {
    "day": (cmd_day, {"offset": {"type": "integer", "description": "0 = hoje, 1 = amanhã, 2 = depois de amanhã..."}}),
    "deadlines": (cmd_deadlines, {"days": {"type": "integer", "description": "quantos dias à frente (7 por defeito)"}}),
    "plan_day": (cmd_plan_day, {}),
    "plan_week": (cmd_plan_week, {}),
    "study_now": (cmd_study_now, {}),
    "materials": (cmd_materials, {"course": {"type": "string", "description": "nome ou sigla da cadeira; vazio = tarefa recomendada"}}),
    "skip_session": (cmd_skip_session, {}),
    "postpone_session": (cmd_postpone_session, {}),
    "focus": (cmd_focus, {"minutes": {"type": "integer", "description": "duração em minutos (50 por defeito)"}}),
    "focus_off": (cmd_focus_off, {}),
    "study_workspace": (cmd_study_workspace, {"course": {"type": "string", "description": "nome ou sigla da cadeira"}}),
    "cancel_desktop": (cmd_cancel_desktop, {}),
    "help": (cmd_help, {}),
}
INTERNAL = {"desktop_confirm": cmd_desktop_confirm, "greet": cmd_greet}  # never offered to the model


def _params(name: str) -> dict[str, dict]:
    return COMMANDS[name][1] if name in COMMANDS else desktop.DESKTOP[name][2]


# --- choosing the command -------------------------------------------------------------------------


def match_rules(text: str, today: date) -> tuple[str, dict[str, Any]]:
    """Keyword matcher (pt-PT), used when the model is offline."""
    t = _fold(text)
    # Commands that change things need a clear verb; anything else falls through to a read.
    if re.search(r"\b(foco|concentr)", t):
        if re.search(r"\b(desliga|desligar|termina|terminar|desativa|desativar|acaba|acabar|sai do|sair do)\b", t):
            return "focus_off", {}
        m = re.search(r"(\d+)\s*(h|hora|horas|min|minutos)\b", t)
        if m or re.search(r"\b(liga|ligar|ativa|ativar|inicia|iniciar|comeca|comecar|modo foco)\b", t):
            return "focus", {"minutes": (int(m[1]) * 60 if m[2].startswith("h") else int(m[1])) if m else 50}
    if re.search(r"\bsess", t) and re.search(r"\b(salta|saltar|cancela|cancelar)\b", t):
        return "skip_session", {}
    if re.search(r"\bsess", t) and (re.search(r"\b(adia|adiar)\b", t) or re.search(r"\b(passa|passar|muda|mudar)\b.*\bpara\b", t)):
        return "postpone_session", {}
    if desktop_rule := match_desktop_rules(t):
        return desktop_rule
    if re.search(r"\b(planeia|planear|planeie|organiza|organizar|organiza-me)\b", t):
        return ("plan_week", {}) if "semana" in t else ("plan_day", {})
    if re.search(r"\b(prazo|entrega|deadline|avaliac|teste|exame|frequenc)", t):
        days = 30 if re.search(r"\bmes\b", t) else int(m[1]) if (m := re.search(r"(\d+)\s*dias", t)) else 7
        return "deadlines", {"days": days}
    if re.search(r"\b(materia|ficheiro|abre|slides|documento)", t):
        m = re.search(r"\b(?:de|da|do|para)\s+(.+)$", t)
        return "materials", {"course": m[1].strip(" ?.!") if m else ""}
    if re.search(r"\b(estudar|estudo|recomend|que faco)", t) and not re.search(r"\b(amanha|hoje)\b", t):
        return "study_now", {}
    if "depois de amanha" in t:
        return "day", {"offset": 2}
    if re.search(r"\bamanha\b", t):
        return "day", {"offset": 1}
    for i, name in enumerate(["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]):
        if re.search(rf"\b{name}\b", t):
            return "day", {"offset": (i - today.weekday()) % 7}
    if re.search(r"\b(hoje|dia|agenda|plano)\b", t):
        return "day", {"offset": 0}
    return "help", {}


def _after(t: str) -> str:
    """What a request is about: the words after "de/do/da/com/sobre", or after the verb and the noun."""
    m = re.search(r"\b(?:de|do|da|dos|das|com|sobre)\s+(.+)$", t)
    return (m[1] if m else "").strip(" ?.!«»\"")


def match_desktop_rules(t: str) -> tuple[str, dict[str, Any]] | None:
    """Desktop requests in pt-PT, for when the model is offline (t is already folded)."""
    if re.fullmatch(r"(cancela|cancelar|para|parar)( (isso|a acao|tudo|o que estas a fazer))?[.!]*", t):
        return "cancel_desktop", {}
    if m := re.search(r"\b(?:vamos|bora|quero|vou|preciso de) (?:la )?estudar (.+)$", t):
        course = m[1].strip(" ?.!")
        if course not in ("agora", "hoje", "ja", "amanha", "o que", "alguma coisa"):
            return "study_workspace", {"course": course}
    if re.search(r"\b(disposicao|layout)\b|\b(arruma|organiza)\w* as janelas\b", t):
        if re.search(r"\b(desfaz|desfazer|repoe|repor|volta|voltar)\b", t):
            return "undo_layout", {}
        name = "programacao" if re.search(r"\bprogram", t) else "estudo" if re.search(r"\bestud", t) else None
        m = re.search(r"\b(?:disposicao|layout)\s+(?:de\s+|chamada\s+)?([a-z0-9][a-z0-9_-]{0,39})\b", t)
        if re.search(r"\b(guarda|guardar|grava|gravar)\b", t):
            return ("save_layout", {"name": m[1]}) if m else None
        return "apply_layout", {"name": name or (m[1] if m else "estudo")}
    if re.search(r"\b(separador|separadores|tab|tabs|aba|abas)\b", t):
        query = _after(t)
        if re.search(r"\b(fecha|fechar)\b", t) and query:
            return "close_tabs", {"query": query}
        if re.search(r"\b(agrupa|agrupar|junta|juntar)\b", t) and query:
            return "group_tabs", {"query": query, "title": query[:40]}
        if re.search(r"\b(mostra|mostrar|vai|ir|abre|abrir|muda|mudar)\b", t) and query:
            return "focus_tab", {"query": query}
        return "find_tabs", {"query": query}
    if m := re.search(r"\bjanela (?:do |da |de |dos |das )?(.+?)(?: para a (?:area|workspace)(?: de trabalho)? (\d{1,2}))?[ ?.!]*$", t):
        window = m[1].strip()
        if m[2] and re.search(r"\b(move|mover|manda|mandar|passa|passar|leva|levar)\b", t):
            return "move_window", {"window": window, "workspace": int(m[2])}
        if re.search(r"\b(fecha|fechar)\b", t):
            return "close_window", {"window": window}
        if re.search(r"\b(foca|focar|mostra|mostrar|vai|ir|muda|mudar)\b", t):
            return "focus_window", {"window": window}
    if m := re.search(r"\b(?:area|workspace|ambiente)(?: de trabalho)?\s+(\d{1,2})\b", t):
        if re.search(r"\b(muda|mudar|vai|ir|passa|passar|mostra|mostrar|abre|abrir)\b", t) and not re.search(r"\bjanela\b", t):
            return "switch_workspace", {"workspace": int(m[1])}
    if re.search(r"\b(materia|ficheiro|slides|documento|apontamento)", t):
        return None  # course files: the materials command answers, with PEACE's own choice of files
    if m := re.fullmatch(r"(?:abre|abrir|lanca|lancar|inicia|iniciar|arranca)\s+(?:o |a |o programa |a aplicacao |a app )?([a-z0-9 ._-]{2,40}?)[.!]*", t):
        return "open_app", {"app": m[1]}
    return None


SYSTEM = (
    "És a PEACE, a secretária académica do utilizador. A tua única função é escolher a ferramenta certa para o "
    "pedido e os seus argumentos. Chama exatamente uma ferramenta; só um pedido para o PC com vários passos pode chamar "
    "várias ferramentas do PC, pela ordem em que devem ser feitas. Nunca inventes caminhos, endereços ou comandos: usa "
    "os nomes que o utilizador disse. Não expliques matéria nem inventes dados. Hoje é {today} ({weekday})."
)


def _tools() -> list[dict[str, Any]]:
    offered = [(name, TOOL_HELP[name], props) for name, (fn, props) in COMMANDS.items()]
    offered += [(name, help_text, props) for name, (tool, help_text, props) in desktop.DESKTOP.items()]
    return [{"type": "function", "function": {"name": name, "description": text,
                                              "parameters": {"type": "object", "properties": props, "required": []}}}
            for name, text, props in offered]


def _call(call: Any) -> tuple[str, dict[str, Any]] | None:
    """One tool call from the model, if it names an offered tool; arguments coerced to their declared types."""
    fn = call.get("function", {}) if isinstance(call, dict) else {}
    name, args = fn.get("name"), fn.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    if (name not in COMMANDS and name not in desktop.DESKTOP) or not isinstance(args, dict):
        return None
    out = {}
    for k, v in args.items():
        kind = _params(name).get(k, {}).get("type")
        if isinstance(v, bool) or kind is None:
            continue
        if kind == "integer" and isinstance(v, (int, str)) and str(v).strip().lstrip("-").isdigit():
            out[k] = int(v)
        elif kind == "string" and isinstance(v, (str, int)):
            out[k] = str(v)[:200 if name in desktop.DESKTOP else 100]
    return name, out


def match_llm(text: str, history: list[AssistantMessage], today: date) -> tuple[str, dict[str, Any]] | None:
    """Ask the local model which command fits. None when it is unreachable or answers off-script."""
    messages = [{"role": "system", "content": SYSTEM.format(today=today.isoformat(), weekday=WEEKDAYS[today.weekday()])}]
    messages += [{"role": "user", "content": m.content} for m in history if m.role == "user"][-3:]
    messages.append({"role": "user", "content": text})
    try:
        r = _llm("POST", "/api/chat", timeout=LLM_TIMEOUT, json={
            "model": LLM_MODEL, "messages": messages, "tools": _tools(), "stream": False, "think": "low",
            "keep_alive": "10m", "options": LLM_OPTIONS,
        })
        r.raise_for_status()
        calls = r.json().get("message", {}).get("tool_calls") or []
    except (httpx.HTTPError, ValueError) as e:
        log.warning("model unavailable: %s", e)
        mark_unreachable()
        return None
    if not calls or not isinstance(calls, list):
        return None
    first = _call(calls[0])
    if first and first[0] in desktop.DESKTOP:
        # A multi-step PC request: the desktop calls in order, at most desktop.MAX_STEPS; anything else is dropped.
        plan = [c for c in map(_call, calls[: desktop.MAX_STEPS]) if c and c[0] in desktop.DESKTOP]
        return ("desktop_plan", {"calls": plan}) if len(plan) > 1 else first
    return first


# --- natural replies: the model talks, the database keeps the facts --------------------------------------

CHAT = (
    "És a PEACE, a secretária académica do utilizador{name}. Conversa com naturalidade e simpatia, em português de "
    "Portugal, em uma a três frases curtas, sem emojis. Nunca afirmes horários, datas, prazos, avaliações, notas ou salas: não os "
    "conheces aqui. Se te pedirem algo assim, sugere que perguntem, por exemplo, «o que tenho amanhã?». O que sabes "
    "fazer: {help}"
)
REWRITE = (
    "Reescreve a mensagem do utilizador como a PEACE, uma secretária académica simpática, a falar com naturalidade em "
    "português de Portugal. Não acrescentes nem retires nenhum facto. Mantém exatamente, tal como estão escritos: todas "
    "as horas (ex.: 16:00), números em algarismos, siglas e nomes de cadeiras, e palavras como hoje, amanhã ou os dias "
    "da semana. Responde só com a mensagem reescrita."
)
REPHRASE = {"day", "deadlines", "study_now", "materials", "plan_day", "plan_week"}  # replies built from data
NUMBER_WORDS = {"um": "1", "uma": "1", "dois": "2", "duas": "2", "tres": "3", "quatro": "4", "cinco": "5", "seis": "6",
                "sete": "7", "oito": "8", "nove": "9", "dez": "10"}  # "uma aula" says the same as "1 aula"
DAY_WORDS = ("hoje", "amanha", "segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")


def _say(messages: list[dict[str, str]]) -> str | None:
    """Free text from the model (no tools). None when it is unreachable or says nothing."""
    try:
        r = _llm("POST", "/api/chat", timeout=LLM_TIMEOUT, json={
            "model": LLM_MODEL, "messages": messages, "stream": False, "think": "low", "keep_alive": "10m",
            "options": LLM_OPTIONS,
        })
        r.raise_for_status()
        text = r.json().get("message", {}).get("content")
    except (httpx.HTTPError, ValueError) as e:
        log.warning("model unavailable: %s", e)
        mark_unreachable()
        return None
    return (text.strip()[:600] or None) if isinstance(text, str) else None


def _facts(text: str) -> tuple[list[str], set[str], set[str]]:
    """Numbers and times (each occurrence: "1 aula" and "Teste 1" are two facts), acronyms (BD, SO, T3) and day words: what a rewrite must keep exactly."""
    acronyms = {w for w in re.findall(r"[\w-]+", text)
                if sum(c.isupper() for c in w) >= 2 or (any(c.isupper() for c in w) and any(c.isdigit() for c in w))}
    folded = _fold(text)
    numbers = re.findall(r"\d+(?::\d\d)?", text) + [NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", folded) if w in NUMBER_WORDS]
    return sorted(numbers), acronyms, {d for d in DAY_WORDS if re.search(rf"\b{d}\b", folded)}


def rephrase(text: str) -> str | None:
    """The data's reply in the model's words, or None when the rewrite changed, lost or added a fact."""
    rewrite = _say([{"role": "system", "content": REWRITE}, {"role": "user", "content": text}])
    return rewrite if rewrite and _facts(rewrite) == _facts(text) else None


def chat(session: Session, text: str, history: list[AssistantMessage]) -> str | None:
    """Conversation outside PEACE's commands. None when the model is away or states a time or date anyway."""
    name = _pref(session, "appearance").get("name", "").strip()
    messages = [{"role": "system", "content": CHAT.format(name=f", {name}" if name else "", help=HELP)}]
    messages += [{"role": m.role, "content": m.content} for m in history[-6:]]
    reply = _say(messages + [{"role": "user", "content": text}])
    if reply and re.search(r"\d{1,2}[:h]\d{2}|\b\d{1,2}h\b|\d{1,2}/\d{1,2}", reply):
        return None  # it has no schedule here: any time or date would be made up
    return reply


# --- state, desktop, status -------------------------------------------------------------------------


def _pref(session: Session, key: str) -> dict[str, Any]:
    row = session.get(Preference, key)
    return dict(row.value) if row else {}


def _put(session: Session, key: str, value: dict[str, Any]) -> None:
    row = session.get(Preference, key)
    if row:
        row.value = value
    else:
        session.add(Preference(key=key, value=value))


def set_state(session: Session, state: str, now: datetime) -> None:
    _put(session, "assistant_state", {"state": state, "at": now.isoformat()})
    session.commit()


def current_state(session: Session, now: datetime) -> str:
    v = _pref(session, "assistant_state")
    state = v.get("state", "idle")
    if state != "idle" and now - datetime.fromisoformat(v["at"]) > timedelta(seconds=STATE_TTL.get(state, 60)):
        return "idle"
    return state


# Commands that change the plan or silence reminders. When only the model (not the rules) reads one of
# these into a request, PEACE asks first: a mis-heard word or a strange title must not delete sessions.
WRITES = {"plan_day", "plan_week", "skip_session", "postpone_session", "focus", "focus_off"}
CONFIRM = {"plan_day": "replanear o resto de hoje", "plan_week": "replanear a semana", "skip_session": "saltar a próxima sessão",
           "postpone_session": "passar a próxima sessão para amanhã", "focus": "ligar o modo foco durante {minutes} min",
           "focus_off": "desligar o modo foco"}
PC_COMMANDS = {"desktop_plan", "study_workspace", "cancel_desktop"}
YES = re.compile(r"^(sim|confirmo|confirma|ok|pode ser|faz isso|claro)\b")
CONFIRM_FOR = timedelta(minutes=2)


def _decide(session: Session, text: str, today: date, now: datetime) -> tuple[str, dict[str, Any], str]:
    """Which command to run, its arguments and who chose it ("rules", "model" or "confirmed")."""
    pending = _pref(session, "assistant_pending")
    _put(session, "assistant_pending", {})
    if pending.get("name") and YES.match(_fold(text).strip()) and now - datetime.fromisoformat(pending["at"]) < CONFIRM_FOR:
        return pending["name"], pending.get("args", {}), "confirmed"
    plain = re.sub(r"[^\w ]+", " ", _fold(text)).split()
    if GREETING.fullmatch(" ".join(plain)) or THANKS.fullmatch(" ".join(plain)):
        return "greet", {"thanks": bool(THANKS.fullmatch(" ".join(plain)))}, "rules"
    history = list(session.scalars(select(AssistantMessage).order_by(AssistantMessage.id.desc()).limit(6)))[::-1]
    picked = match_llm(text, history, today) if llm_reachable() else None
    rules = match_rules(text, today)
    if not picked:
        return *rules, "rules"
    acts_on_pc = picked[0] in desktop.DESKTOP or picked[0] in PC_COMMANDS
    if acts_on_pc and rules[0] not in desktop.DESKTOP and rules[0] not in PC_COMMANDS:
        if rules[0] != "help":
            return *rules, "rules"  # a question the rules recognise is answered, never turned into a PC action
        # Only the model heard a PC action (it also sees earlier requests): ask before touching the desktop.
        _put(session, "assistant_pending", {"name": picked[0], "args": picked[1], "at": now.isoformat()})
        return "confirm", picked[1] | {"_what": picked[0]}, "model"
    if picked[0] in WRITES and rules[0] != picked[0]:
        _put(session, "assistant_pending", {"name": picked[0], "args": picked[1], "at": now.isoformat()})
        return "confirm", picked[1] | {"_what": picked[0]}, "model"
    return *picked, "model"


def ask(session: Session, text: str, source: str, now: datetime) -> dict[str, Any]:
    today = now.astimezone(routes._tz()).date()
    set_state(session, "thinking", now)
    history = list(session.scalars(select(AssistantMessage).order_by(AssistantMessage.id.desc()).limit(6)))[::-1]
    name, args, via = _decide(session, text, today, now)
    session.add(AssistantMessage(role="user", content=text, source=source))
    if name == "confirm":
        what = args.pop("_what")
        if what in CONFIRM:
            text = CONFIRM[what].format(minutes=args.get("minutes", 50))
        elif what == "desktop_plan":
            text = "; ".join(DESCRIBE[n].format_map(_Unknown(a)) for n, a in args["calls"])
        elif what in DESCRIBE:
            text = DESCRIBE[what].format_map(_Unknown(args))
        else:
            text = {"study_workspace": f"preparar o PC para estudar {args.get('course', '?')}", "cancel_desktop": "cancelar a ação no PC"}[what]
        reply = Reply(f"Confirmas: {text}? Responde «sim».", "confirm")
    elif name == "desktop_plan":
        reply = cmd_desktop_plan(session, now, args["calls"])
    elif name in desktop.DESKTOP:
        reply = cmd_desktop(session, now, name, args)
    elif name in INTERNAL:
        reply = INTERNAL[name](session, now, **args)
    else:
        reply = COMMANDS[name][0](session, now, **args)
    if reply.intent == "help" and llm_reachable():
        reply.text = chat(session, text, history) or reply.text
    elif name in REPHRASE and llm_reachable():
        reply.text = rephrase(reply.text) or reply.text
    session.add(AssistantMessage(role="assistant", content=reply.text, source=source, intent=reply.intent,
                                 data={"items": reply.items, "to": reply.to, "via": via}))
    # Voice: the desktop moves on to "speaking"; typed: done.
    set_state(session, "thinking" if source == "voice" else "idle", now)
    return {"reply": reply.text, "intent": reply.intent, "items": reply.items, "to": reply.to, "via": via,
            "model": LLM_MODEL if via != "rules" else None, "job": reply.job}


def status(session: Session, now: datetime) -> dict[str, Any]:
    desktop = _pref(session, "desktop")
    seen = datetime.fromisoformat(desktop["seen_at"]) if desktop.get("seen_at") else None
    connected = bool(seen and now - seen < timedelta(seconds=DESKTOP_TTL))
    caps = desktop.get("capabilities", {}) if connected else {}
    until = focus_until(session, now)
    return {
        "state": current_state(session, now),
        "model": {"name": LLM_MODEL, "reachable": llm_reachable()},
        "desktop": {"connected": connected, "seen_at": seen, "actions": caps.get("actions", {})},
        "voice": {"available": bool(caps.get("voice")), "name": caps.get("voice_name"), "shortcut": caps.get("ptt"),
                  "voices": caps.get("voices", []), "audio": caps.get("audio", {})},
        "focus": {"active": until is not None, "until": until},
    }


# --- endpoints ---------------------------------------------------------------------------------------


class AskIn(s.In):
    text: str = Field(min_length=1, max_length=500)
    source: Literal["text", "voice"] = "text"


class StateIn(s.In):
    state: States


class FocusIn(s.In):
    minutes: int = Field(50, ge=5, le=240)


class AudioDevice(s.In):
    id: str = Field(max_length=200)
    name: str = Field(max_length=200)


class AudioCapabilities(s.In):
    inputs: list[AudioDevice] = Field(default_factory=list, max_length=100)
    outputs: list[AudioDevice] = Field(default_factory=list, max_length=100)
    error: str | None = Field(None, max_length=300)
    ptt_bound: bool = False
    capture_available: bool = False
    playback_available: bool = False
    stt_available: bool = False
    latency: dict[str, int] = Field(default_factory=dict, max_length=10)


class ActionCapabilities(s.In):
    categories: list[Literal["files", "apps", "windows", "browser", "layouts"]] = Field(default_factory=list, max_length=5)
    hyprland: bool = False
    browser: bool = False


class Capabilities(BaseModel):
    voice: bool = False
    voice_name: str | None = Field(None, max_length=100)
    ptt: str | None = Field(None, max_length=40)
    llm: bool = False
    voices: list[str] = Field([], max_length=20)  # the speech engine's voices, for the Settings picker
    audio: AudioCapabilities = Field(default_factory=AudioCapabilities)
    actions: ActionCapabilities = Field(default_factory=ActionCapabilities)


class HeartbeatIn(s.In):
    capabilities: Capabilities = Capabilities()


class VoiceTestIn(s.In):
    action: Literal["meter", "stt", "tts"]
    settings: routes.VoicePrefs


class VoiceTestResult(s.In):
    state: Literal["running", "done", "error"]
    level: float = Field(0, ge=0, le=1)
    message: str = Field("", max_length=300)


@router.post("/voice/test", status_code=202)
def start_voice_test(body: VoiceTestIn, session: Session = DB, now: datetime = NOW):
    """An explicit user action authorizes only this five-second diagnostic, never a conversation."""
    desktop = _pref(session, "desktop")
    seen = datetime.fromisoformat(desktop["seen_at"]) if desktop.get("seen_at") else None
    if not seen or now - seen >= timedelta(seconds=DESKTOP_TTL):
        raise HTTPException(409, "O agente do PC está desligado.")
    old = _pref(session, "voice_test")
    if old.get("state") in ("pending", "running") and now < datetime.fromisoformat(old["expires_at"]):
        raise HTTPException(409, "Já existe um teste de voz em curso.")
    value = {"id": uuid.uuid4().hex, **body.model_dump(), "state": "pending", "level": 0,
             "message": "À espera do agente…", "expires_at": (now + timedelta(seconds=15)).isoformat(),
             "capture_before": (now + timedelta(seconds=15)).isoformat()}
    _put(session, "voice_test", value)
    session.commit()
    return value


@router.get("/voice/test")
def voice_test_status(session: Session = DB, now: datetime = NOW):
    value = _pref(session, "voice_test")
    if value.get("state") in ("pending", "running") and now >= datetime.fromisoformat(value["expires_at"]):
        return {"id": value["id"], "state": "error", "message": "Teste expirado; inicia outro teste.", "level": 0}
    return value or None


@router.get("/desktop/voice-test")
def pending_voice_test(session: Session = DB, now: datetime = NOW):
    row = session.scalar(select(Preference).where(Preference.key == 'voice_test').with_for_update())
    value = dict(row.value) if row else {}
    if value.get("state") != "pending" or now >= datetime.fromisoformat(value["expires_at"]):
        return None
    # Claim before recording: an agent restart cannot replay an old consent action.
    value["state"] = "running"
    value["expires_at"] = (now + timedelta(minutes=3)).isoformat()
    _put(session, "voice_test", value)
    session.commit()
    return value


@router.post("/desktop/voice-test/{test_id}", status_code=204)
def voice_test_result(test_id: str, body: VoiceTestResult, session: Session = DB, now: datetime = NOW):
    row = session.scalar(select(Preference).where(Preference.key == 'voice_test').with_for_update())
    value = dict(row.value) if row else {}
    if value.get("id") != test_id or value.get('state') != 'running' or now >= datetime.fromisoformat(value["expires_at"]):
        raise HTTPException(409, "Teste expirado.")
    value.update(body.model_dump())
    if body.state in ("done", "error"):
        value.pop("settings", None)
    _put(session, "voice_test", value)
    session.commit()


@router.post("/assistant/ask")
def ask_endpoint(body: AskIn, session: Session = DB, now: datetime = NOW):
    return ask(session, body.text.strip(), body.source, now)


@router.get("/assistant/messages")
def list_messages(limit: int = Query(20, ge=1, le=200), session: Session = DB):
    rows = session.scalars(select(AssistantMessage).order_by(AssistantMessage.id.desc()).limit(limit)).all()
    return [{"id": m.id, "role": m.role, "text": m.content, "source": m.source, "created_at": m.created_at,
             "items": m.data.get("items", []), "to": m.data.get("to")} for m in reversed(rows)]


@router.get("/assistant/status")
def status_endpoint(session: Session = DB, now: datetime = NOW):
    return status(session, now)


@router.post("/assistant/state", status_code=204)
def state_endpoint(body: StateIn, session: Session = DB, now: datetime = NOW):
    set_state(session, body.state, now)


@router.post("/desktop/heartbeat", status_code=204)
def heartbeat(body: HeartbeatIn, session: Session = DB, now: datetime = NOW):
    _put(session, "desktop", {"seen_at": now.isoformat(), "capabilities": body.capabilities.model_dump()})
    session.commit()


@router.post("/focus")
def focus_on(body: FocusIn, session: Session = DB, now: datetime = NOW):
    set_focus(session, now, body.minutes)
    return status(session, now)["focus"]


@router.delete("/focus")
def focus_off(session: Session = DB, now: datetime = NOW):
    set_focus(session, now, None)
    return status(session, now)["focus"]

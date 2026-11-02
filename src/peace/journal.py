"""PEACE's daily note for Obsidian: the day's classes, assessments, deliveries, study plan and site news.

The worker rewrites today's note while the day runs; earlier days stay as they were at their end. The files live in
the data folder and the desktop agent copies them into the vault (07 - Generated/Peace/Diário), like Graphify's
notes, never over a note the student edited.
"""

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from peace import library, routes
from peace.models import Change, Event

WEEKDAYS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
          "novembro", "dezembro"]


def daily_dir() -> Path:
    return library.data_dir() / "daily"


def note(session: Session, day: date, now: datetime, tz: ZoneInfo) -> str:
    start, end = routes._day_bounds(day, tz)
    hm = lambda at: at.astimezone(tz).strftime("%H:%M")  # noqa: E731
    events = session.scalars(select(Event).where(Event.starts_at < end, Event.ends_at > start).order_by(Event.starts_at)).all()
    sessions = [s for s in routes._sessions_between(session, start, end) if s.status in ("planned", "done")]
    due = [d for d in routes._deadline_items(session, now, max(2, (day - now.astimezone(tz).date()).days + 2))
           if d["type"] != "exam" and start <= d["due_at"] < end]
    # The first import is marked seen as it is created: that is not news.
    news = session.scalars(select(Change).where(Change.created_at >= start, Change.created_at < end, or_(
        Change.seen_at.is_(None), Change.seen_at > Change.created_at + timedelta(minutes=1))).order_by(Change.id)).all()

    lines = ["---", f"date: {day.isoformat()}", "tags: [peace, diario]", "---",
             f"# {WEEKDAYS[day.weekday()]}, {day.day} de {MONTHS[day.month - 1]} de {day.year}", ""]
    section = lambda title, rows, empty: lines.extend([f"## {title}", *(rows or [empty]), ""])  # noqa: E731
    section("Aulas e avaliações", [f"- {hm(e.starts_at)}–{hm(e.ends_at)} {'**Avaliação:** ' if e.kind == 'exam' else ''}{e.title}"
                                   + (f" · {e.location}" if e.location else "") for e in events], "Sem aulas nem avaliações.")
    section("Entregas", [f"- [ ] {hm(d['due_at'])} {d['title']}" for d in due], "Nenhuma entrega.")
    section("Estudo", [f"- [{'x' if s.status == 'done' else ' '}] {hm(s.starts_at)}–{hm(s.ends_at)} "
                       f"{s.task.title if s.task else s.event.title if s.event else 'Sessão livre'}" for s in sessions],
            "Nada planeado.")
    section("Novidades nos sites", [f"- {c.summary}" for c in news[:20]], "Nada de novo.")
    lines.append("*Gerada pela PEACE. Para escrever as tuas notas do dia, usa outra nota: se mudares esta, a PEACE deixa de a atualizar.*")
    return "\n".join(lines) + "\n"


def write_today(session: Session, now: datetime, tz: ZoneInfo) -> bool:
    """Rewrite today's note if it changed. True when it was written."""
    day = now.astimezone(tz).date()
    path = daily_dir() / f"{day.isoformat()}.md"
    text = note(session, day, now, tz)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".part").write_text(text, encoding="utf-8")
    path.with_suffix(".part").replace(path)
    return True

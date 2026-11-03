"""Background worker: daily sync at 02:00 local time (Europe/Lisbon by default).

The schedule lives in the database, not in memory: after a restart or downtime the worker
catches up once, failed runs are retried a few times, and the advisory lock plus a re-check
under that lock guarantee one scheduled run per source per day, even with two workers.
"""

import logging
import signal
import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from peace.config import get_settings
from peace.db import get_engine
from peace.logs import configure_logging
from peace.knowledge import build_graph, communities, export_graphify
from peace.library import fetch_pending, mark_pages, remove_orphans
from peace import archives, filelinks, improve, proposals, studyplan, updates
from peace.topics import analyze_pending
from peace import workload
from peace.models import SyncRun
from peace.notifications import generate, worker_alive
from peace.phone import push_due
from peace.journal import write_today
from peace import google_calendar, mail, mail_extract, practice, requirements
from peace.sync import RUN_AT, SOURCES, AlreadyRunning, last_slot, next_slot, run_sync  # noqa: F401 (slots re-exported)

log = logging.getLogger(__name__)
RETRY_AFTER = timedelta(minutes=15)
MAX_ATTEMPTS = 3
EXPORT_EVERY = timedelta(minutes=30)


def is_due(session: Session, source: str, now: datetime, tz: ZoneInfo) -> bool:
    runs = session.scalars(
        select(SyncRun)
        .where(SyncRun.source == source, SyncRun.trigger == "scheduled", SyncRun.started_at >= last_slot(now, tz))
        .order_by(SyncRun.id)
    ).all()
    if any(r.status in ("success", "auth_failed") for r in runs) or len(runs) >= MAX_ATTEMPTS:
        return False  # done, or retrying would risk locking the account
    return not runs or (runs[-1].finished_at or runs[-1].started_at) + RETRY_AFTER <= now


def recover_interrupted(session: Session, now: datetime) -> None:
    """Runs left 'running' by a crash: their lock died with the process."""
    session.execute(
        update(SyncRun)
        .where(SyncRun.status == "running", SyncRun.started_at < now - timedelta(hours=1))
        .values(status="failed", error="interrupted", finished_at=now)
    )
    session.commit()


def tick(tz: ZoneInfo) -> None:
    for source in SOURCES:
        now = datetime.now(UTC)
        try:
            run_sync(source, "scheduled", now=now, precheck=lambda s, src=source: is_due(s, src, now, tz))
        except AlreadyRunning:
            log.info("sync %s already running; will check again", source)
    with Session(get_engine()) as session:
        worker_alive(session, datetime.now(UTC))
        if done := proposals.auto_apply(session, datetime.now(UTC)):
            log.info("applied %d proposals", len(done))
        if made := generate(session, datetime.now(UTC), tz):
            log.info("created %d notifications", made)
        session.commit()
        if pushed := push_due(session, datetime.now(UTC)):
            log.info("sent %d notifications to the phone", pushed)
        try:
            write_today(session, datetime.now(UTC), tz)
        except OSError as e:
            log.warning("daily note not written: %s", e)
        refresh_knowledge(session, datetime.now(UTC))
        check_mail(session, datetime.now(UTC))
        try:
            if read := requirements.revised_briefs(session, datetime.now(UTC)):  # only while the model is reachable
                log.info("read %d revised assignment briefs again", read)
        except Exception:
            session.rollback()
            log.exception("re-reading revised briefs failed")
        practise(session, datetime.now(UTC))


def practise(session: Session, now: datetime) -> None:
    """Mark written answers left waiting for the model, then add questions to the bank (phase 7, while the model may load)."""
    try:
        if marked := practice.grade_waiting(session, now):
            log.info("marked %d written answers", marked)
        if made := practice.generate_due(session, now):
            log.info("wrote %d practice questions (kept and discarded)", made)
    except Exception:
        session.rollback()
        log.exception("practice questions failed")  # the next tick retries


def check_mail(session: Session, now: datetime) -> None:
    """Read the university mailbox every 15 minutes and offer the weekly cleanup (phase 4)."""
    try:
        mail.run(session, now)
        if mail.offer_cleanup(session, now):
            session.commit()
        if made := mail_extract.extract_pending(session, now):  # only while the model is reachable
            log.info("found %d dates in university emails", made)
    except Exception:
        session.rollback()
        log.exception("mail check failed")  # the next tick retries; nothing else depends on it


_last_export: dict[str, datetime] = {}  # per-process; a restart simply exports again


def refresh_knowledge(session: Session, now: datetime) -> None:
    """Download and analyze new files, then rebuild the graph and Graphify's export when needed."""
    try:
        fetched = fetch_pending(session, now)
        if fetched:
            log.info("analyzed %d course files", fetched)
            remove_orphans(session)
        if fetched:
            archives.expand_pending(session, now)
            filelinks.mark_datasets(session)
            filelinks.mark_duplicates(session)
        filelinks.refresh_literal(session)
        if marked := mark_pages(session):
            log.info("added page marks to %d course files", marked)
        if read := analyze_pending(session, now):  # only while the desktop's model is reachable
            log.info("read topics of %d course files", read)
        if studyplan.adapt_all(session, now):
            session.commit()
        updates.daily_check(session, now)
        if read := workload.analyze_pending(session, now):
            log.info("read %d project briefs or exam guides", read)
        finished = improve.step(session, now)
        last = _last_export.get("at")
        if fetched or finished or last is None or now - last >= EXPORT_EVERY:
            G = build_graph(session, now)
            export_graphify(G, communities(G))
            _last_export["at"] = now
    except Exception:
        log.exception("knowledge refresh failed")  # the next tick retries; syncing is unaffected


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    tz = ZoneInfo(settings.timezone)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    with Session(get_engine()) as session:
        recover_interrupted(session, datetime.now(UTC))
    log.info("worker started; next scheduled sync at %s", next_slot(datetime.now(UTC), tz).isoformat())
    threading.Thread(target=google_calendar.run_forever, args=(stop,), name="google-calendar", daemon=True).start()
    while not stop.is_set():
        try:
            tick(tz)
        except Exception:
            log.exception("worker tick failed")  # keep the worker alive; the next tick retries
        stop.wait(60)

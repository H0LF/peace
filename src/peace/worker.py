"""Background worker: daily sync at 02:00 local time (Europe/Lisbon by default).

The schedule lives in the database, not in memory: after a restart or downtime the worker
catches up once, failed runs are retried a few times, and the advisory lock plus a re-check
under that lock guarantee one scheduled run per source per day, even with two workers.
"""

import logging
import signal
import threading
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from peace.config import get_settings
from peace.db import get_engine
from peace.logs import configure_logging
from peace.knowledge import build_graph, communities, export_graphify
from peace.library import fetch_pending, remove_orphans
from peace.topics import analyze_pending
from peace.models import SyncRun
from peace.notifications import generate
from peace.sync import SOURCES, AlreadyRunning, run_sync

log = logging.getLogger(__name__)
RUN_AT = time(2, 0)
RETRY_AFTER = timedelta(minutes=15)
MAX_ATTEMPTS = 3
EXPORT_EVERY = timedelta(minutes=30)


def last_slot(now: datetime, tz: ZoneInfo) -> datetime:
    local = now.astimezone(tz)
    slot = datetime.combine(local.date(), RUN_AT, tz)
    if slot > local:
        slot = datetime.combine(local.date() - timedelta(days=1), RUN_AT, tz)
    return slot.astimezone(UTC)


def next_slot(now: datetime, tz: ZoneInfo) -> datetime:
    local = now.astimezone(tz)
    slot = datetime.combine(local.date(), RUN_AT, tz)
    if slot <= local:
        slot = datetime.combine(local.date() + timedelta(days=1), RUN_AT, tz)
    return slot.astimezone(UTC)


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
        if made := generate(session, datetime.now(UTC), tz):
            log.info("created %d notifications", made)
        session.commit()
        refresh_knowledge(session, datetime.now(UTC))


_last_export: dict[str, datetime] = {}  # per-process; a restart simply exports again


def refresh_knowledge(session: Session, now: datetime) -> None:
    """Download and analyze new files, then rebuild the graph and Graphify's export when needed."""
    try:
        fetched = fetch_pending(session, now)
        if fetched:
            log.info("analyzed %d course files", fetched)
            remove_orphans(session)
        if read := analyze_pending(session, now):  # only while the desktop's model is reachable
            log.info("read topics of %d course files", read)
        last = _last_export.get("at")
        if fetched or last is None or now - last >= EXPORT_EVERY:
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
    while not stop.is_set():
        try:
            tick(tz)
        except Exception:
            log.exception("worker tick failed")  # keep the worker alive; the next tick retries
        stop.wait(60)

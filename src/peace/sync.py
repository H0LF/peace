"""Synchronization engine: one locked, atomic run per source. Deterministic, no LLM involved.

A run either applies everything it fetched or nothing (rollback). Missing data never deletes
assignments; timetable events that vanish from a successfully fetched window are removed.
"""

import logging
import os
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import exists, func, select, text
from sqlalchemy.orm import Session

from peace.changes import record_removal, upsert_from_source
from peace.db import get_engine
from peace.models import Change, Course, Event, Material, Preference, SyncRun, Task
from peace.sources import AuthError, SourceError, inforestudante, moodle

log = logging.getLogger(__name__)
SOURCES = ("inforestudante", "moodle")
SOURCE_MODULES = {"inforestudante": inforestudante, "moodle": moodle}  # inforestudante first: it provides course codes and class groups
STALE_AFTER = timedelta(hours=36)


class AlreadyRunning(Exception):
    pass


@contextmanager
def source_lock(source: str) -> Iterator[None]:
    """Postgres advisory lock, so the API (manual) and worker (scheduled) never sync a source twice at once."""
    key = {"key": f"peace-sync:{source}"}
    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        if not conn.scalar(text("SELECT pg_try_advisory_lock(hashtext(:key))"), key):
            raise AlreadyRunning(source)
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), key)


def is_running(source: str) -> bool:
    try:
        with source_lock(source):
            return False
    except AlreadyRunning:
        return True


def integrations(session: Session) -> dict[str, dict[str, Any]]:
    """Settings → Integrações, per source: on/off and the login. An empty login falls back to the env file."""
    row = session.get(Preference, "integrations")
    saved = row.value if row else {}
    out = {}
    for source in SOURCES:
        # A source without an address (MOODLE_URL / INFORESTUDANTE_URL) starts switched off.
        v = {"enabled": bool(SOURCE_MODULES[source].BASE), "username": "", "password": ""} | saved.get(source, {})
        v["username"] = v["username"] or os.environ.get(f"{source.upper()}_USERNAME", "")
        v["password"] = v["password"] or os.environ.get(f"{source.upper()}_PASSWORD", "")
        out[source] = v
    return out


def enabled_sources(session: Session) -> list[str]:
    return [src for src, v in integrations(session).items() if v["enabled"]]


def _credentials(source: str) -> tuple[str, str]:
    if not SOURCE_MODULES[source].BASE:
        raise AuthError(f"{source.upper()}_URL is not configured")
    with Session(get_engine()) as session:
        login = integrations(session)[source]
    if not login["username"] or not login["password"]:
        raise AuthError(f"{source} credentials are not configured")
    return login["username"], login["password"]


def fetch_inforestudante(now: datetime) -> inforestudante.Snapshot:
    client = inforestudante.Inforestudante(*_credentials("inforestudante"))
    try:
        return client.snapshot(now.astimezone(inforestudante.LISBON).date())
    finally:
        client.http.close()


def fetch_moodle(now: datetime) -> moodle.Snapshot:
    client = moodle.Moodle(*_credentials("moodle"))
    try:
        return client.snapshot(now)
    finally:
        client.http.close()


FETCHERS: dict[str, Callable[[datetime], Any]] = {"inforestudante": fetch_inforestudante, "moodle": fetch_moodle}


def _norm(name: str) -> str:
    plain = unicodedata.normalize("NFKD", moodle.YEAR_PREFIX.sub("", name)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain).strip().lower()


def apply_inforestudante(session: Session, snap: inforestudante.Snapshot) -> list[Change | None]:
    source = "inforestudante"
    changes: list[Change | None] = []
    for c in snap.courses:
        if session.scalar(select(Course).filter_by(source=source, external_id=c["code"])) is None:
            # A course first seen on Moodle (e.g. inforestudante was down) becomes this one: no duplicates.
            twins = session.scalars(select(Course).where(Course.source == "moodle")).all()
            twin = next((t for t in twins if t.code == c["code"] or _norm(t.name) == _norm(c["name"])), None)
            if twin is not None:
                twin.source, twin.external_id, twin.source_data = source, c["code"], None
                session.flush()
        fields = {"name": c["name"], "code": c["code"], "class_groups": ", ".join(c["groups"]) or None}
        changes.append(upsert_from_source(session, Course, source, c["code"], fields))

    course_ids = {c.code: c.id for c in session.scalars(select(Course).where(Course.code.is_not(None)))}
    seen = set()
    for e in snap.events:
        fields = {k: e[k] for k in ("kind", "title", "starts_at", "ends_at", "location")}
        fields["course_id"] = course_ids.get(e["course_code"])
        changes.append(upsert_from_source(session, Event, source, e["external_id"], fields, record_created=e["kind"] == "exam"))
        seen.add(e["external_id"])

    stale = session.scalars(
        select(Event).where(
            Event.source == source,
            Event.starts_at >= snap.window_start,
            Event.starts_at < snap.window_end,
            Event.external_id.not_in(seen),
        )
    ).all()
    if stale and not snap.events:
        raise SourceError(f"timetable came back empty; refusing to remove {len(stale)} events")
    for event in stale:
        if event.kind == "exam":
            changes.append(record_removal(session, source, event))
        session.delete(event)
    by_code = {c.code: c for c in session.scalars(select(Course).where(Course.code.is_not(None)))}
    changes += apply_materials(session, source, snap.materials, lambda m: by_code.get(m["course_code"]))
    return changes


def apply_moodle(session: Session, snap: moodle.Snapshot) -> list[Change | None]:
    source = "moodle"
    changes: list[Change | None] = []
    known = session.scalars(select(Course)).all()
    by_code = {c.code: c for c in known if c.code}
    by_name = {_norm(c.name): c for c in known}
    resolved: dict[int, Course] = {}
    for mc in snap.courses:
        course = by_code.get(mc["code"]) or by_name.get(_norm(mc["name"]))
        if course is None:
            fields = {"name": mc["name"], "code": mc["code"], "url": mc["url"]}
            changes.append(upsert_from_source(session, Course, source, str(mc["id"]), fields))
            course = session.scalar(select(Course).filter_by(source=source, external_id=str(mc["id"])))
        elif course.url is None:
            course.url = mc["url"]
        resolved[mc["id"]] = course

    groups = {mid: {g.strip() for g in (c.class_groups or "").split(",") if g.strip()} for mid, c in resolved.items()}
    for t in moodle.collapse_group_copies(snap.tasks, groups):
        course = resolved.get(t["moodle_course"])
        done = t["submitted_at"] is not None
        fields = {
            "title": t["title"],
            "description": t["description"],
            "kind": "assignment",
            "due_at": t["due_at"],
            "url": t["url"],
            "course_id": course.id if course else None,
            "status": "COMPLETED" if done else "TODO",
            "progress": 100 if done else 0,
            "completed_at": t["submitted_at"],
        }
        # Work that was already submitted when first seen is not news.
        changes.append(upsert_from_source(session, Task, source, t["external_id"], fields, record_created=not done))

    changes += apply_materials(session, source, snap.materials, lambda m: resolved.get(m["moodle_course"]))
    return changes


def apply_materials(
    session: Session, source: str, items: list[dict[str, Any]], course_of: Callable[[dict[str, Any]], Course | None]
) -> list[Change | None]:
    """Upsert one source's materials; vanished ones lose their row (vault copies are never deleted)."""
    # The first time a source's materials are imported, a course full of old slides is not news.
    first = not session.scalar(select(exists().where(Material.source == source)))
    changes: list[Change | None] = []
    used: set[str] = {p.lower() for p in session.scalars(select(Material.vault_path).where(
        Material.source != source, Material.vault_path.is_not(None)))}
    seen = set()
    for m in sorted(items, key=lambda m: m["external_id"]):
        course = course_of(m)
        fields = {k: m[k] for k in ("section", "title", "kind", "filename", "size", "mimetype", "modified_at", "url", "file_url")}
        fields["course_id"] = course.id if course else None
        fields["vault_path"] = vault_path(course.name if course else None, m, used) if m["kind"] == "file" else None
        changes.append(upsert_from_source(session, Material, source, m["external_id"], fields, record_created=not first))
        seen.add(m["external_id"])
    gone = session.scalars(select(Material).where(Material.source == source, Material.external_id.not_in(seen))).all()
    if gone and not items:
        raise SourceError(f"{source}: materials came back empty; refusing to forget {len(gone)}")
    for material in gone:
        session.delete(material)
    return changes


UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')


def _safe(name: str, limit: int = 120) -> str:
    """One path segment: no separators, no characters Obsidian rejects, never hidden or '..'."""
    cleaned = UNSAFE.sub("-", name).strip().lstrip(".").strip()
    return cleaned[:limit].strip() or "sem nome"


def vault_path(course: str | None, m: dict[str, Any], used: set[str]) -> str:
    """Where the desktop stores a file, relative to the vault. Same-name files get " (2)", " (3)"..."""
    folders = ["02 - University", "Courses", _safe(course or "Sem cadeira"), "Materiais", _safe(m["section"])]
    folders += [_safe(m["folder"])] if m["folder"] else []
    folders += [_safe(part) for part in m["subpath"].split("/") if part not in ("", ".", "..")]
    stem, dot, ext = m["filename"].rpartition(".")
    stem, ext = (stem, f".{_safe(ext, 10)}") if dot and stem else (m["filename"], "")
    path, n = "/".join(folders + [_safe(stem, 140) + ext]), 2
    while path.lower() in used:
        path, n = "/".join(folders + [f"{_safe(stem, 140)} ({n}){ext}"]), n + 1
    used.add(path.lower())
    return path


_moodle_token: dict[str, str] = {}  # shortcut: per-process token cache; re-login on a refused download


def open_moodle_file(url: str) -> tuple[httpx.Response, httpx.Client]:
    """Start streaming a Moodle file with the student's token, which never leaves the server.
    The caller closes both the response and the client."""
    if not url.startswith(moodle.PLUGINFILE):
        raise SourceError("not a Moodle file URL")
    client = httpx.Client(timeout=120, follow_redirects=True)
    for retry in (False, True):
        if retry or "token" not in _moodle_token:
            login = moodle.Moodle(*_credentials("moodle"), http=client)
            login.login()
            _moodle_token["token"] = login.token
        response = client.send(client.build_request("GET", url, params={"token": _moodle_token["token"]}), stream=True)
        # Moodle answers a bad or expired token with a JSON error instead of the file.
        if response.status_code == 200 and "json" not in response.headers.get("content-type", ""):
            return response, client
        response.close()
    client.close()
    raise SourceError("Moodle refused the download")


APPLY: dict[str, Callable[[Session, Any], list[Change | None]]] = {
    "inforestudante": apply_inforestudante,
    "moodle": apply_moodle,
}


def run_sync(
    source: str,
    trigger: str,
    now: datetime | None = None,
    fetch: Callable[[datetime], Any] | None = None,
    precheck: Callable[[Session], bool] | None = None,
) -> SyncRun | None:
    """Run one sync. Raises AlreadyRunning; returns None when `precheck` (run under the lock) says no."""
    now = now or datetime.now(UTC)
    fetch = fetch or FETCHERS[source]
    with source_lock(source), Session(get_engine(), expire_on_commit=False) as session:
        if source not in enabled_sources(session) or (precheck is not None and not precheck(session)):
            return None  # switched off in Settings, or not due
        first = not session.scalar(select(exists().where(SyncRun.source == source, SyncRun.status == "success")))
        run = SyncRun(source=source, trigger=trigger, started_at=now)
        session.add(run)
        session.commit()
        try:
            snap = fetch(now)
            changes = [c for c in APPLY[source](session, snap) if c is not None]
            if first:
                for c in changes:
                    c.seen_at = now  # the initial import is not news
            counts = {k: len(getattr(snap, k)) for k in ("courses", "events", "tasks", "materials") if hasattr(snap, k)}
            run.stats = {"items": counts, "changes": dict(Counter(c.kind for c in changes)), "skipped": getattr(snap, "skipped", {})}
            run.status = "success"
        except AuthError as e:
            session.rollback()
            run.status, run.error = "auth_failed", str(e)[:2000]
        except Exception as e:
            session.rollback()
            log.exception("sync %s failed", source)
            run.status, run.error = "failed", f"{type(e).__name__}: {e}"[:2000]
        run.finished_at = datetime.now(UTC)
        session.commit()
        log.info("sync %s (%s): %s %s", source, trigger, run.status, run.stats or run.error)
        return run


def run_all(trigger: str) -> None:
    for source in SOURCES:
        try:
            run_sync(source, trigger)
        except AlreadyRunning:
            log.info("sync %s already running; skipped", source)


def status(session: Session, now: datetime, next_run: datetime) -> list[dict[str, Any]]:
    """Per-source health for the dashboard. Data is flagged stale; it is never presented as fresh."""
    out = []
    for source in enabled_sources(session):  # a source switched off in Settings is not shown at all
        last = session.scalar(select(SyncRun).where(SyncRun.source == source).order_by(SyncRun.id.desc()).limit(1))
        ok = session.scalar(
            select(func.max(SyncRun.finished_at)).where(SyncRun.source == source, SyncRun.status == "success")
        )
        out.append({
            "source": source,
            "last_status": last.status if last else None,
            "last_run_at": last.started_at if last else None,
            "last_error": last.error if last and last.status != "success" else None,
            "last_success_at": ok,
            "stale": ok is None or now - ok > STALE_AFTER,
            "needs_login": bool(last and last.status == "auth_failed"),
            "next_scheduled_at": next_run,
        })
    return out

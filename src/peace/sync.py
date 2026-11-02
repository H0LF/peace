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
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, exists, func, select, text
from sqlalchemy.orm import Session

from peace.changes import record_removal, upsert_from_source
from peace.config import get_settings
from peace.db import get_engine
from peace.models import Change, Course, Event, Material, Preference, SyncRun, Task
from peace.planner import SMALL_WORK
from peace.sources import AuthError, SourceError, inforestudante, moodle

log = logging.getLogger(__name__)
SOURCES = ("inforestudante", "moodle")
SOURCE_MODULES = {"inforestudante": inforestudante, "moodle": moodle}  # inforestudante first: it provides course codes and class groups
STALE_AFTER = timedelta(hours=36)


RUN_AT = time(2, 0)  # the daily sync, Europe/Lisbon by default (the worker runs it)


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


# Students paste whatever page they have open; keep the site's base address.
MOODLE_PAGES = re.compile(r"/(?:login|my|course|mod|user|admin|webservice|calendar|grade|message)(?:/.*)?$|/index\.php.*$")


def site_address(source: str, url: str) -> str:
    """"…/moodle/login/index.php" -> "…/moodle"; "…/nonio/security/login.do" -> "…/nonio"."""
    url = url.strip().split("?")[0].split("#")[0].rstrip("/")
    if source == "inforestudante" and "/nonio" in url:
        return url[: url.index("/nonio") + len("/nonio")]
    return MOODLE_PAGES.sub("", url).rstrip("/") if source == "moodle" else url


def integrations(session: Session) -> dict[str, dict[str, Any]]:
    """Settings → Integrações, per source: on/off, address and login. Empty fields fall back to the env file."""
    row = session.get(Preference, "integrations")
    saved = row.value if row else {}
    out = {}
    for source in SOURCES:
        v = {"url": "", "username": "", "password": ""} | saved.get(source, {})
        v["url"] = site_address(source, v["url"] or os.environ.get(f"{source.upper()}_URL", ""))
        v.setdefault("enabled", bool(v["url"]))  # a source without an address starts switched off
        v["username"] = v["username"] or os.environ.get(f"{source.upper()}_USERNAME", "")
        v["password"] = v["password"] or os.environ.get(f"{source.upper()}_PASSWORD", "")
        out[source] = v
    return out


def mail_login(session: Session) -> dict[str, Any]:
    """Settings → Integrações → Email (phase 4): the webmail address the student knows, the login, and cleanup options.
    Stored with the other sites' logins; IMAP runs on the webmail's host."""
    row = session.get(Preference, "integrations")
    v = {"enabled": False, "url": "", "username": "", "password": "", "clean_junk": True, "quota_mb": None}
    v |= (row.value if row else {}).get("email", {})
    v["host"] = urlsplit(v["url"]).hostname or ""
    return v


def enabled_sources(session: Session) -> list[str]:
    return [src for src, v in integrations(session).items() if v["enabled"]]


def _credentials(source: str) -> tuple[str, str]:
    """The login for a source, after pointing its adapter at the address from Settings."""
    with Session(get_engine()) as session:
        login = integrations(session)[source]
    if not login["url"]:
        raise AuthError(f"{source} address is not configured")
    SOURCE_MODULES[source].configure(login["url"])
    if not login["username"] or not login["password"]:
        raise AuthError(f"{source} credentials are not configured")
    return login["username"], login["password"]


def fetch_inforestudante(now: datetime) -> inforestudante.Snapshot:
    client = inforestudante.Inforestudante(*_credentials("inforestudante"))
    try:
        return client.snapshot(now.astimezone(inforestudante.LISBON).date())
    finally:
        client.http.close()


def download_inforestudante(wanted: dict[str, Path], max_bytes: int) -> dict[str, str]:
    """Download files by external id into the given paths. Returns the errors, by external id."""
    client = inforestudante.Inforestudante(*_credentials("inforestudante"))
    try:
        client.login()
        return client.download_materials(wanted, max_bytes)
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


def dropped_courses(session: Session) -> set[str]:
    row = session.get(Preference, "dropped_courses")
    return set(row.value) if row else set()


def drop_course(session: Session, course: Course) -> None:
    """The student left this course but the sites still list it: forget its data and never import it again.
    Files are removed by library.remove_orphans; the vault belongs to the desktop agent."""
    name = _norm(course.name)
    row = session.get(Preference, "dropped_courses") or session.merge(Preference(key="dropped_courses", value=[]))
    row.value = sorted({*row.value, name})
    for model, entity in ((Task, "task"), (Event, "event"), (Material, "material")):
        ids = session.scalars(select(model.id).where(model.course_id == course.id)).all()
        session.execute(delete(Change).where(Change.entity == entity, Change.entity_id.in_(ids)))
        session.execute(delete(model).where(model.id.in_(ids)))
    session.execute(delete(Change).where(Change.entity == "course", Change.entity_id == course.id))
    session.delete(course)


def _twin_delivery(session: Session, course_id: int | None, due: datetime, sources: tuple[str, ...]) -> Task | None:
    """A task of this course due the same day: two sites naming one delivery (their times differ by minutes)."""
    tz = ZoneInfo(get_settings().timezone)
    rows = session.scalars(select(Task).where(Task.course_id == course_id, Task.source.in_(sources), Task.due_at.is_not(None)))
    return next((t for t in rows if t.due_at.astimezone(tz).date() == due.astimezone(tz).date() and not SMALL_WORK.search(t.title)), None)


def _apply_delivery(session: Session, e: dict[str, Any], course_id: int | None) -> Change | None:
    """A delivery from the timetable becomes a task, unless Moodle or a read brief already has it."""
    if _twin_delivery(session, course_id, e["starts_at"], ("moodle", "brief")):
        return None
    fields = {"title": e["title"], "kind": "assignment", "due_at": e["starts_at"], "course_id": course_id}
    return upsert_from_source(session, Task, "inforestudante", e["external_id"], fields)


def apply_inforestudante(session: Session, snap: inforestudante.Snapshot) -> list[Change | None]:
    source = "inforestudante"
    changes: list[Change | None] = []
    dropped = dropped_courses(session)
    gone = {c["code"] for c in snap.courses if _norm(c["name"]) in dropped}
    for c in snap.courses:
        if c["code"] in gone:
            continue
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
        if e["course_code"] in gone:
            continue
        if e["kind"] == "delivery":
            changes.append(_apply_delivery(session, e, course_ids.get(e["course_code"])))
            continue
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
    materials = [m for m in snap.materials if m["course_code"] not in gone]
    changes += apply_materials(session, source, materials, lambda m: by_code.get(m["course_code"]))
    return changes


def apply_moodle(session: Session, snap: moodle.Snapshot) -> list[Change | None]:
    source = "moodle"
    changes: list[Change | None] = []
    known = session.scalars(select(Course)).all()
    by_code = {c.code: c for c in known if c.code}
    by_name = {_norm(c.name): c for c in known}
    resolved: dict[int, Course] = {}
    dropped = dropped_courses(session)
    gone = {c["id"] for c in snap.courses if _norm(c["name"]) in dropped}
    for mc in snap.courses:
        if mc["id"] in gone:
            continue
        course = by_code.get(mc["code"]) or by_name.get(_norm(mc["name"]))
        if course is None:
            fields = {"name": mc["name"], "code": mc["code"], "url": mc["url"]}
            changes.append(upsert_from_source(session, Course, source, str(mc["id"]), fields))
            course = session.scalar(select(Course).filter_by(source=source, external_id=str(mc["id"])))
        elif course.url is None:
            course.url = mc["url"]
        resolved[mc["id"]] = course

    groups = {mid: {g.strip() for g in (c.class_groups or "").split(",") if g.strip()} for mid, c in resolved.items()}
    for t in moodle.collapse_group_copies([t for t in snap.tasks if t["moodle_course"] not in gone], groups):
        course = resolved.get(t["moodle_course"])
        done = t["submitted_at"] is not None
        if course and t["due_at"] and not SMALL_WORK.search(t["title"]) and (
                twin := _twin_delivery(session, course.id, t["due_at"], ("inforestudante",))):
            session.delete(twin)  # the timetable only knew the date; Moodle knows the rest, and whether it was submitted
            session.flush()
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

    materials = [m for m in snap.materials if m["moodle_course"] not in gone]
    changes += apply_materials(session, source, materials, lambda m: resolved.get(m["moodle_course"]))
    posts = None if snap.posts is None else [p for p in snap.posts if p["moodle_course"] not in gone]
    changes += apply_posts(session, source, posts, resolved)
    return changes


def apply_posts(session: Session, source: str, posts: list[dict[str, Any]] | None, courses: dict[int, Course]) -> list[Change | None]:
    """Each announcement or forum post becomes one news item, once (keyed by its discussion id). Posts are never
    deleted here: a failed or partial read (posts None) changes nothing."""
    if not posts:
        return []
    known = set(session.scalars(select(Change.entity_id).where(Change.source == source, Change.entity == "announcement")))
    first = not known  # the first read of a course's forums is not news
    out: list[Change | None] = []
    for p in posts:
        if p["external_id"] in known:
            continue
        known.add(p["external_id"])
        course = courses.get(p["moodle_course"])
        where = f" de {course.name}" if course else ""
        change = Change(source=source, entity="announcement", entity_id=p["external_id"], kind="created",
                        summary=(f"Aviso{where}: " if p["forum"] == "news" else f"Fórum{where}: ") + p["subject"],
                        detail={k: p[k] for k in ("forum", "excerpt", "author", "created", "url")} | {"course_id": course.id if course else None},
                        seen_at=datetime.now(UTC) if first else None)
        session.add(change)
        out.append(change)
    return out


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
    credentials = _credentials("moodle")  # also sets the address that PLUGINFILE is checked against
    if not url.startswith(moodle.PLUGINFILE):
        raise SourceError("not a Moodle file URL")
    client = httpx.Client(timeout=120, follow_redirects=True)
    for retry in (False, True):
        if retry or "token" not in _moodle_token:
            login = moodle.Moodle(*credentials, http=client)
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
            else:
                from peace import proposals  # proposals imports the routes, which import this module

                proposals.from_changes(session, changes, now)
            counts = {k: len(getattr(snap, k)) for k in ("courses", "events", "tasks", "materials") if hasattr(snap, k)}
            run.stats = {"items": counts, "changes": dict(Counter(c.kind for c in changes)), "skipped": getattr(snap, "skipped", {})}
            run.status = "success"
        except AuthError as e:
            session.rollback()
            run.status, run.error = "auth_failed", str(e)[:2000]
        except Exception as e:
            session.rollback()
            log.exception("sync %s failed", source)
            run.status, run.error = "failed", re.sub(r"(token=)[^&\s]+", r"\1***", f"{type(e).__name__}: {e}")[:2000]
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

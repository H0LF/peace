"""Two-way sync with a "PEACE" calendar in the owner's Google account; their other calendars come in read-only.

A reconcile loop instead of hooks in every route: each round compares what PEACE has with what it last sent (a hash
per row in google_links) and pushes only differences, so nothing is lost if PEACE stops mid-way. Pushed events carry a
fixed Google id derived from the PEACE row, so a retry never duplicates. Updates send If-Match with Google's etag: an
event edited in Google meanwhile is never overwritten; the pull (incremental, syncToken) decides, newest edit wins.
See ADR-018.
"""

import hashlib
import json
import logging
import os
import threading
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from peace import google, routes
from peace.auth import require_token
from peace.changes import NEW, _jsonable, upsert_from_source
from peace.classify import ask_model, classify
from peace.db import get_engine, get_session
from peace.models import Change, Course, Event, GoogleLink, Preference, StudySession, Task

API = "https://www.googleapis.com/calendar/v3"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
# Narrow scopes: manage only calendars PEACE creates; read (never change) the user's other calendars.
SCOPES = ("https://www.googleapis.com/auth/calendar.app.created",
          "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
          "https://www.googleapis.com/auth/calendar.events.readonly")
PREF = "google_calendar"
PUSH_SECONDS = 15  # PEACE -> Google feels immediate
PULL_SECONDS = 120  # Google -> PEACE; Google cannot push to a PC that is not on the internet
DEADLINE = timedelta(minutes=30)  # a deadline shows as a short event ending at the due time
RETRIES = 3
# Local-model classifications per pull. Beyond it, events keep the rules' answer (appointment) until their title
# changes or the user corrects the type: the first import of a busy calendar must not hold the GPU for minutes.
MODEL_PER_ROUND = 20
KIND_COLOR = {"exam": "11", "class": "9"}  # Google's tomato and blueberry

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/google", dependencies=[Depends(require_token)])
_access: dict[str, Any] = {}
_sleep = time.sleep


class GoogleError(Exception):
    pass


class Reconnect(GoogleError):
    """Google refused the saved permission (revoked, expired): the user must connect again."""


class Forbidden(Reconnect):
    """403 on one resource: a missing scope (reconnect) or a calendar shared with too little access (skip it)."""


# --- the saved permission (a 600 file, never the database: backups never carry a key to the Google account) ---------

def config_dir() -> Path:
    if path := os.environ.get("PEACE_CONFIG_DIR"):
        return Path(path)
    return Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "peace"


def token_file() -> Path:
    return config_dir() / "google.json"


def _save_token(data: dict[str, Any]) -> None:
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _load_token() -> dict[str, Any] | None:
    try:
        return json.loads(token_file().read_text())
    except (OSError, ValueError):
        return None


def _http(method: str, url: str, **kw: Any) -> httpx.Response:
    return httpx.request(method, url, timeout=20, **kw)


def access_token() -> str:
    saved = _load_token()
    if not saved:
        raise Reconnect("no saved permission")
    # Keyed by the saved permission: the API process may reconnect (another account) while the worker holds a token.
    if _access.get("refresh") == saved["refresh_token"] and _access.get("until", 0) > time.monotonic():
        return _access["token"]
    client_id, secret = google.client()
    r = _http("POST", google.TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": saved["refresh_token"],
                                              "client_id": client_id, "client_secret": secret})
    if r.status_code in (400, 401):
        raise Reconnect(r.text[:200])
    r.raise_for_status()
    body = r.json()
    _access.update(token=body["access_token"], refresh=saved["refresh_token"],
                   until=time.monotonic() + int(body.get("expires_in", 3600)) - 60)
    return _access["token"]


def _rate_limited(r: httpx.Response) -> bool:
    try:
        return any(e.get("reason") in ("rateLimitExceeded", "userRateLimitExceeded")
                   for e in r.json()["error"]["errors"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def call(method: str, path: str, **kw: Any) -> httpx.Response:
    """Calendar API with back-off on rate limits and server errors. 404/409/410/412 are answers, not failures."""
    headers = kw.pop("headers", None) or {}
    for attempt in range(RETRIES):
        r = _http(method, API + path, headers={"Authorization": f"Bearer {access_token()}", **headers}, **kw)
        if r.status_code == 401:
            _access.clear()
            continue
        if r.status_code == 429 or r.status_code >= 500 or (r.status_code == 403 and _rate_limited(r)):
            _sleep(min(float(r.headers.get("retry-after") or 2 ** attempt), 30))
            continue
        if r.status_code < 400 or r.status_code in (404, 409, 410, 412):
            return r
        if r.status_code == 403:
            raise Forbidden(f"{method} {path}: permission missing")
        raise GoogleError(f"{method} {path}: HTTP {r.status_code}")
    raise GoogleError(f"{method} {path}: still failing after {RETRIES} attempts")


def _q(calendar_id: str) -> str:
    return quote(calendar_id, safe="")


# --- preferences ---------------------------------------------------------------------------------------------------

def prefs(session: Session) -> dict[str, Any]:
    row = session.get(Preference, PREF)
    return dict(row.value) if row else {}


def _save_prefs(session: Session, value: dict[str, Any]) -> None:
    row = session.get(Preference, PREF)
    if row:
        row.value = value
    else:
        session.add(Preference(key=PREF, value=value))


def connected(session: Session) -> bool:
    return bool(prefs(session).get("connected")) and token_file().exists()


def connect(session: Session, tokens: dict[str, Any], claims: dict[str, Any], now: datetime) -> None:
    """After Google's consent: keep the permission, find or create the "PEACE" calendar, start from today."""
    if not tokens.get("refresh_token") or not set(SCOPES) <= set(tokens.get("scope", "").split()):
        raise GoogleError("permission not granted")
    _save_token({"refresh_token": tokens["refresh_token"], "email": claims.get("email", ""), "sub": claims["sub"]})
    _access.update(token=tokens["access_token"], refresh=tokens["refresh_token"],
                   until=time.monotonic() + int(tokens.get("expires_in", 3600)) - 60)
    p = prefs(session)
    calendar_id = p.get("calendar_id")
    if not calendar_id or call("GET", f"/calendars/{_q(calendar_id)}").status_code == 404:
        r = call("POST", "/calendars", json={"summary": "PEACE", "timeZone": routes._tz().key,
                                             "description": "Aulas, avaliações, prazos e eventos da PEACE."})
        calendar_id = r.json()["id"]
        session.execute(delete(GoogleLink))  # they pointed into a calendar that no longer exists
    today = datetime.combine(now.astimezone(routes._tz()).date(), dtime(), routes._tz())
    _save_prefs(session, {"connected": True, "calendar_id": calendar_id, "email": claims.get("email", ""),
                          "since": today.astimezone(UTC).isoformat(), "sessions": p.get("sessions", False),
                          "sync_tokens": {}, "readonly": [], "error": None, "last_sync": None})
    routes._audit(session, "google.calendar.connect", email=claims.get("email", ""))
    session.commit()


def disconnect(session: Session, delete_calendar: bool) -> None:
    """Stop syncing and forget the permission. PEACE keeps all its own data; the user's other calendars' copies go."""
    p = prefs(session)
    if delete_calendar and p.get("calendar_id"):
        try:
            call("DELETE", f"/calendars/{_q(p['calendar_id'])}")
        except (GoogleError, httpx.HTTPError) as e:
            log.warning("PEACE calendar not deleted in Google: %s", e)
    if saved := _load_token():
        try:
            _http("POST", REVOKE_URL, data={"token": saved["refresh_token"]})
        except httpx.HTTPError as e:
            log.warning("Google permission not revoked: %s", e)
    token_file().unlink(missing_ok=True)
    _access.clear()
    session.execute(delete(GoogleLink))
    session.execute(delete(Event).where(Event.source == "google").execution_options(include_cancelled=True))
    # A kept calendar is reused on reconnect: its events carry their PEACE ids and are linked again.
    _save_prefs(session, {"sessions": p.get("sessions", False),
                          "calendar_id": None if delete_calendar else p.get("calendar_id")})
    routes._audit(session, "google.calendar.disconnect", deleted_calendar=delete_calendar)
    session.commit()


# --- PEACE -> Google -----------------------------------------------------------------------------------------------

def _hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _when(at: datetime) -> dict[str, str]:
    return {"dateTime": at.astimezone(UTC).isoformat()}


def google_id(entity: str, entity_id: int) -> str:
    return f"peace{entity[0]}{entity_id}"  # Google ids use a-v and 0-9: "peacee12", "peacet7", "peaces3"


def event_body(e: Event) -> dict[str, Any]:
    # No description: an event made in Google keeps the user's own text.
    body = {"summary": e.title, "location": e.location or "", "start": _when(e.starts_at), "end": _when(e.ends_at),
            "extendedProperties": {"private": {"peace": f"event:{e.id}", "peace_kind": e.kind}}}
    if e.kind in KIND_COLOR:
        body["colorId"] = KIND_COLOR[e.kind]
    return body


def task_body(t: Task) -> dict[str, Any]:
    mark = "✅" if t.status == "COMPLETED" else "📌"
    assert t.due_at is not None
    return {"summary": f"{mark} Entrega: {t.title}", "start": _when(t.due_at - DEADLINE), "end": _when(t.due_at),
            "transparency": "transparent", "description": f"Prazo{' de ' + t.course.name if t.course else ''} (PEACE)",
            "extendedProperties": {"private": {"peace": f"task:{t.id}"}}}


def session_body(s: StudySession) -> dict[str, Any]:
    what = s.task.title if s.task else s.event.title if s.event else "sessão de estudo"
    if s.topic:
        what += f": {s.topic}"
    return {"summary": f"📚 Estudo: {what}", "start": _when(s.starts_at), "end": _when(s.ends_at),
            "description": "Sessão de estudo planeada pela PEACE",
            "extendedProperties": {"private": {"peace": f"session:{s.id}"}}}


BODY = {"event": event_body, "task": task_body, "session": session_body}
MODEL = {"event": Event, "task": Task, "session": StudySession}


def desired(session: Session, p: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    """Every PEACE row that belongs in the Google calendar, from the day of connection on."""
    since = datetime.fromisoformat(p["since"])
    want: dict[tuple[str, int], dict[str, Any]] = {}
    for e in session.scalars(select(Event).where(Event.source != "google", Event.ends_at >= since)):
        want["event", e.id] = event_body(e)
    for t in session.scalars(select(Task).where(Task.due_at >= since, Task.status != "CANCELLED")):
        want["task", t.id] = task_body(t)
    if p.get("sessions"):
        for s in session.scalars(select(StudySession).where(StudySession.starts_at >= since,
                                                            StudySession.status != "skipped")):
            want["session", s.id] = session_body(s)
    return want


def _insert(base: str, gid: str, body: dict[str, Any]) -> httpx.Response:
    r = call("POST", base, json={**body, "id": gid})
    if r.status_code == 409:  # the id exists (an earlier attempt, or deleted before): update brings it back
        r = call("PUT", f"{base}/{gid}", json={**body, "status": "confirmed"})
    return r


def push(session: Session, p: dict[str, Any]) -> int:
    """Send what changed. One event Google refuses is logged and skipped, never blocking the others."""
    base = f"/calendars/{_q(p['calendar_id'])}/events"
    want = desired(session, p)
    links = {(link.entity, link.entity_id): link for link in session.scalars(select(GoogleLink))}
    pushed, refused = 0, 0
    for key, body in want.items():
        digest, link = _hash(body), links.get(key)
        if link and (link.suppressed or link.payload_hash == digest):
            continue
        try:
            if link:
                # PATCH, not PUT: reminders, attendees and the description the user added in Google stay.
                r = call("PATCH", f"{base}/{link.google_id}", json=body,
                         headers={"If-Match": link.etag} if link.etag else {})
                if r.status_code in (404, 410, 412):
                    continue  # changed or deleted in Google meanwhile: the next pull decides
            else:
                link = GoogleLink(entity=key[0], entity_id=key[1], google_id=google_id(*key))
                session.add(link)
                r = _insert(base, link.google_id, body)
        except Forbidden:
            raise
        except GoogleError as e:
            session.rollback()
            log.warning("Google refused %s %s: %s", *key, e)
            refused += 1
            continue
        link.etag, link.payload_hash = r.json()["etag"], digest
        session.commit()
        pushed += 1
    if refused:
        raise GoogleError(f"{refused} events refused by Google")
    for key, link in links.items():
        if key not in want:
            if not link.suppressed:
                call("DELETE", f"{base}/{link.google_id}")  # 404/410: already gone
            session.delete(link)
            session.commit()
    return pushed


# --- Google -> PEACE -----------------------------------------------------------------------------------------------

def _time(value: dict[str, str]) -> datetime:
    if "dateTime" in value:
        return datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
    return datetime.combine(date.fromisoformat(value["date"]), dtime(), routes._tz())  # all-day


def _list(calendar_id: str, p: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None] | None:
    """Changes since the last pull (everything since the connection the first time). None: the calendar is gone."""
    params: dict[str, Any] = {"singleEvents": "true", "showDeleted": "true", "maxResults": 250}
    if token := p["sync_tokens"].get(calendar_id):
        params["syncToken"] = token
    else:
        params["timeMin"] = p["since"]
    items: list[dict[str, Any]] = []
    while True:
        r = call("GET", f"/calendars/{_q(calendar_id)}/events", params=params)
        if r.status_code == 410:  # the sync token expired: list everything again
            p["sync_tokens"].pop(calendar_id, None)
            return _list(calendar_id, p)
        if r.status_code == 404:
            return None
        body = r.json()
        items += body.get("items", [])
        if not body.get("nextPageToken"):
            return items, body.get("nextSyncToken")
        params["pageToken"] = body["nextPageToken"]


def _courses(session: Session) -> list[tuple[int, str | None, str]]:
    return [(c.id, c.code, c.name) for c in session.scalars(select(Course))]


def _note(session: Session, entity: str, entity_id: int, summary: str, detail: dict[str, Any]) -> None:
    session.add(Change(source="google", entity=entity, entity_id=entity_id, kind="updated", summary=summary,
                       detail=detail))


def _google_fields(entity: str, row: Any, item: dict[str, Any]) -> dict[str, Any]:
    starts, ends = _time(item["start"]), _time(item["end"])
    if entity == "task":
        return {"due_at": ends}
    if entity == "session":
        return {"starts_at": starts, "ends_at": max(ends, starts + timedelta(minutes=1))}
    kind = (item.get("extendedProperties") or {}).get("private", {}).get("peace_kind")
    return {"title": (item.get("summary") or "(sem título)")[:300], "starts_at": starts, "ends_at": max(ends, starts),
            "location": (item.get("location") or "")[:200] or None, "kind": kind if kind in ("class", "exam", "appointment") else row.kind}


def _deleted_in_google(session: Session, link: GoogleLink) -> None:
    row = session.get(MODEL[link.entity], link.entity_id)
    if link.entity == "task":
        link.suppressed = True  # the user does not want this deadline in Google; the task itself is untouched
        return
    session.delete(link)
    if row is None:
        return
    if link.entity == "session":
        row.status = "skipped"
    elif row.source == "manual":
        session.delete(row)
    else:  # an imported class or exam: cancelled, so the next university sync does not bring it back
        row.cancelled = True
        _note(session, "event", row.id, f"Cancelado no Google: {row.title}", {})


def _changed_in_google(session: Session, link: GoogleLink, item: dict[str, Any]) -> None:
    row = session.get(MODEL[link.entity], link.entity_id)
    if row is None:  # deleted in PEACE meanwhile: delete it in Google too, or it would be orphaned
        call("DELETE", f"/calendars/{_q(prefs(session)['calendar_id'])}/events/{link.google_id}")
        session.delete(link)
        return
    ours_changed = _hash(BODY[link.entity](row)) != link.payload_hash
    google_at = datetime.fromisoformat(item["updated"].replace("Z", "+00:00"))
    label = getattr(row, "title", None) or "sessão de estudo"
    if ours_changed and row.updated_at and row.updated_at > google_at:
        link.etag = item["etag"]  # PEACE's newer edit wins; the next push may now overwrite Google's version
        _note(session, link.entity, row.id, f"Editado nos dois lados, ficou a versão da PEACE: {label}", {})
        return
    fields = _google_fields(link.entity, row, item)
    diff = {k: [_jsonable(getattr(row, k)), _jsonable(v)] for k, v in fields.items() if getattr(row, k) != v}
    for k, v in fields.items():
        setattr(row, k, v)
    session.flush()
    if diff:
        what = "Editado nos dois lados, ficou a versão do Google" if ours_changed else "Alterado no Google"
        _note(session, link.entity, row.id, f"{what}: {label}", diff)
    link.etag, link.payload_hash = item["etag"], _hash(BODY[link.entity](row))


def _new_in_google(session: Session, item: dict[str, Any], courses: list, model: Any, base: str) -> None:
    peace = (item.get("extendedProperties") or {}).get("private", {}).get("peace", "")
    entity, _, raw_id = peace.partition(":")
    if entity in MODEL and raw_id.isdigit():
        # One of PEACE's own (a kept calendar after reconnecting, or a restored database): link it again.
        if session.get(MODEL[entity], int(raw_id)) is None:
            call("DELETE", f"{base}/{item['id']}")
        elif session.scalar(select(GoogleLink).filter_by(entity=entity, entity_id=int(raw_id))) is None:
            session.add(GoogleLink(entity=entity, entity_id=int(raw_id), google_id=item["id"], etag=item["etag"]))
        return
    starts, ends = _time(item["start"]), _time(item["end"])
    kind, course_id = classify(item.get("summary") or "", item.get("description") or "", courses, model)
    event = Event(title=(item.get("summary") or "(sem título)")[:300], kind=kind, course_id=course_id,
                  starts_at=starts, ends_at=max(ends, starts), location=(item.get("location") or "")[:200] or None)
    session.add(event)
    session.flush()
    # No payload hash: the next push stamps PEACE's id on the Google event (a PATCH, the user's text stays), so a kept
    # calendar is linked again after reconnecting instead of imported twice.
    session.add(GoogleLink(entity="event", entity_id=event.id, google_id=item["id"], etag=item["etag"]))
    if kind == "exam":
        session.add(Change(source="google", entity="event", entity_id=event.id, kind="created",
                           summary=f"{NEW['exam']}: {event.title}", detail={}))


def _pull_peace_calendar(session: Session, p: dict[str, Any], model: Any) -> None:
    calendar_id = p["calendar_id"]
    listed = _list(calendar_id, p)
    if listed is None:
        raise Reconnect("the PEACE calendar was deleted in Google")
    items, token = listed
    links = {link.google_id: link for link in session.scalars(select(GoogleLink))}
    courses, base = _courses(session), f"/calendars/{_q(calendar_id)}/events"
    for item in items:
        link = links.get(item["id"])
        if link is not None and item.get("etag") == link.etag:
            continue  # our own write coming back
        if item.get("status") == "cancelled":
            if link is not None:
                _deleted_in_google(session, link)
        elif link is not None:
            _changed_in_google(session, link, item)
        else:
            _new_in_google(session, item, courses, model, base)
    p["sync_tokens"][calendar_id] = token
    session.commit()


def _short(calendar_id: str) -> str:
    return hashlib.sha256(calendar_id.encode()).hexdigest()[:12]


def _pull_readonly(session: Session, p: dict[str, Any], model: Any) -> None:
    """The user's other calendars, as read-only events the planner works around."""
    # shortcut: one page (250 calendars); paginate if anyone ever has more
    r = call("GET", "/users/me/calendarList", params={"minAccessRole": "reader", "maxResults": 250})
    calendars = [c["id"] for c in r.json().get("items", [])
                 if c["id"] != p["calendar_id"] and c.get("selected") and not c.get("hidden")]
    for gone in set(p.get("readonly", [])) - set(calendars):
        session.execute(delete(Event).where(Event.source == "google", Event.external_id.startswith(f"{_short(gone)}:"))
                        .execution_options(include_cancelled=True))
        p["sync_tokens"].pop(gone, None)
    courses = _courses(session)
    for calendar_id in calendars:
        full = calendar_id not in p["sync_tokens"]
        try:
            listed = _list(calendar_id, p)
        except Forbidden as e:  # shared with too little access: skip it, the others still sync
            log.warning("calendar %s skipped: %s", _short(calendar_id), e)
            continue
        if listed is None:
            continue
        items, token = listed
        if full and p["sync_tokens"].get(calendar_id) is None:
            # A full listing does not report deletions: drop copies of this calendar that it no longer has.
            seen = [f"{_short(calendar_id)}:{i['id']}"[:255] for i in items]
            session.execute(delete(Event).where(Event.source == "google",
                                                Event.external_id.startswith(f"{_short(calendar_id)}:"),
                                                Event.external_id.not_in(seen)).execution_options(include_cancelled=True))
        for item in items:
            external_id = f"{_short(calendar_id)}:{item['id']}"[:255]
            # A copy made by another PEACE on this account (a second install) is not the student's own commitment.
            other_peace = bool(item.get("extendedProperties", {}).get("private", {}).get("peace"))
            if item.get("status") == "cancelled" or item.get("transparency") == "transparent" or other_peace:
                session.execute(delete(Event).where(Event.source == "google", Event.external_id == external_id)
                                .execution_options(include_cancelled=True))
                continue
            starts, ends = _time(item["start"]), _time(item["end"])
            kind, course_id = classify(item.get("summary") or "", item.get("description") or "", courses, model)
            upsert_from_source(session, Event, "google", external_id, {
                "title": (item.get("summary") or "(ocupado)")[:300], "kind": kind, "course_id": course_id,
                "starts_at": starts, "ends_at": max(ends, starts), "location": (item.get("location") or "")[:200] or None,
            }, record_created=kind == "exam")
        p["sync_tokens"][calendar_id] = token
        session.commit()
    p["readonly"] = calendars
    session.commit()


def run_round(session: Session, now: datetime, pull: bool) -> None:
    p = prefs(session)
    if not p.get("connected") or not token_file().exists():
        return
    p.setdefault("sync_tokens", {})
    budget = [MODEL_PER_ROUND]

    def model(title: str, description: str) -> str | None:
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        return ask_model(title, description)
    error = None
    try:
        if pull:
            _pull_peace_calendar(session, p, model)
            _pull_readonly(session, p, model)
        push(session, p)
    except Reconnect as e:
        log.warning("Google Calendar needs reconnecting: %s", e)
        error = "reconnect"
    except (GoogleError, httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("Google Calendar sync failed: %s", e)
        error = "google"
    session.rollback()
    latest = prefs(session)  # the user may have changed a setting during the round
    if latest.get("connected"):
        latest.update(sync_tokens=p["sync_tokens"], readonly=p.get("readonly", []), error=error,
                      last_sync=now.isoformat() if error is None else latest.get("last_sync"))
        _save_prefs(session, latest)
        session.commit()


def run_forever(stop: threading.Event) -> None:
    """The worker's thread: push every 15 s, pull every 2 min."""
    last_pull = 0.0
    while not stop.is_set():
        pull = time.monotonic() - last_pull >= PULL_SECONDS
        try:
            with Session(get_engine()) as session:
                run_round(session, datetime.now(UTC), pull)
            if pull:
                last_pull = time.monotonic()
        except Exception:
            log.exception("Google Calendar round failed")  # the next round retries
        stop.wait(PUSH_SECONDS)


# --- Settings ------------------------------------------------------------------------------------------------------

class CalendarPrefs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sessions: bool


@router.get("/calendar")
def calendar_status(session: Session = Depends(get_session)):
    p = prefs(session)
    return {"available": bool(google.client()[0]), "connected": connected(session), "email": p.get("email"),
            "sessions": bool(p.get("sessions")), "last_sync": p.get("last_sync"), "error": p.get("error"),
            "readonly_calendars": len(p.get("readonly") or [])}


@router.put("/calendar")
def calendar_prefs(body: CalendarPrefs, session: Session = Depends(get_session)):
    p = prefs(session)
    if not p.get("connected"):
        raise HTTPException(409, "O Google Calendar não está ligado.")
    _save_prefs(session, {**p, "sessions": body.sessions})
    routes._audit(session, "google.calendar.prefs", sessions=body.sessions)
    session.commit()
    return calendar_status(session)


@router.delete("/calendar", status_code=204)
def calendar_disconnect(delete_calendar: bool = False, session: Session = Depends(get_session)):
    disconnect(session, delete_calendar)

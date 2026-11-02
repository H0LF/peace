"""Two-way Google Calendar sync against a fake Calendar API (ADR-018)."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from conftest import TOKEN
from peace import api, google, google_calendar as gc
from peace.changes import upsert_from_source
from peace.models import Change, Course, Event, GoogleLink, StudySession, Task

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
CAL = "peace-cal@group.calendar.google.com"


class FakeGoogle:
    """Just enough of Google's Calendar API: etags, If-Match, sync tokens, deleted ids, rate limits."""

    def __init__(self):
        self.calendars = {"primary": {}}  # id -> {event id -> item}
        self.listed = [{"id": "primary", "selected": True}]
        self.version = 0
        self.calls = []
        self.fail_next = []  # status codes to answer before the real one
        self.refresh_ok = True
        self.revoked = []

    def _stamp(self, item):
        self.version += 1
        item["etag"] = f'"{self.version}"'
        item["_v"] = self.version
        item["updated"] = (NOW + timedelta(seconds=self.version)).isoformat().replace("+00:00", "Z")
        return item

    def add(self, calendar, item):
        item = self._stamp({"status": "confirmed", **item})
        self.calendars[calendar][item["id"]] = item
        return item

    def edit(self, calendar, gid, **fields):
        item = self.calendars[calendar][gid]
        item.update(fields)
        return self._stamp(item)

    def __call__(self, method, url, **kw):
        r = self.answer(method, url, **kw)
        r.request = httpx.Request(method, url)
        return r

    def answer(self, method, url, **kw):
        self.calls.append((method, url))
        if url == google.TOKEN_URL:
            ok = self.refresh_ok
            return httpx.Response(200 if ok else 400, json={"access_token": "at", "expires_in": 3600} if ok else {"error": "invalid_grant"})
        if url == gc.REVOKE_URL:
            self.revoked.append(kw["data"]["token"])
            return httpx.Response(200)
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), headers={"retry-after": "0"})
        path = urlparse(url).path.removeprefix("/calendar/v3")
        parts = [p.replace("%40", "@") for p in path.strip("/").split("/")]
        if parts[:2] == ["users", "me"]:
            return httpx.Response(200, json={"items": self.listed})
        if parts == ["calendars"] and method == "POST":
            self.calendars[CAL] = {}
            return httpx.Response(200, json={"id": CAL})
        cal = parts[1]
        if len(parts) == 2:
            if cal not in self.calendars:
                return httpx.Response(404)
            if method == "DELETE":
                del self.calendars[cal]
            return httpx.Response(200 if method == "GET" else 204, json={"id": cal} if method == "GET" else None)
        if cal not in self.calendars:
            return httpx.Response(404)
        events = self.calendars[cal]
        if len(parts) == 3 and method == "GET":
            params = kw.get("params") or {}
            if params.get("syncToken") == "expired":
                return httpx.Response(410)
            since = int(params.get("syncToken", 0))
            items = [{k: v for k, v in i.items() if k != "_v"} for i in events.values() if i["_v"] > since]
            return httpx.Response(200, json={"items": items, "nextSyncToken": str(self.version)})
        if len(parts) == 3 and method == "POST":
            body = kw["json"]
            if body["id"] in events:
                return httpx.Response(409)
            if body.get("summary") == "REJECT":
                return httpx.Response(400)
            return httpx.Response(200, json=self.add(cal, dict(body)))
        gid = parts[3]
        item = events.get(gid)
        if item is None:
            return httpx.Response(404)
        if method == "DELETE":
            if item["status"] == "cancelled":
                return httpx.Response(410)
            item["status"] = "cancelled"
            self._stamp(item)
            return httpx.Response(204)
        if method in ("PUT", "PATCH"):
            match = (kw.get("headers") or {}).get("If-Match")
            if match and match != item["etag"]:
                return httpx.Response(412)
            if kw["json"].get("summary") == "REJECT":
                return httpx.Response(400)
            base = {k: v for k, v in item.items() if k != "_v"} if method == "PATCH" else {}
            new = {**base, "id": gid, "status": "confirmed", **kw["json"]}
            events[gid] = self._stamp(new)
            return httpx.Response(200, json=new)
        return httpx.Response(405)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    fake = FakeGoogle()
    monkeypatch.setenv("PEACE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PEACE_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(gc, "_http", fake)
    monkeypatch.setattr(gc, "_sleep", lambda s: None)
    monkeypatch.setattr(gc, "ask_model", lambda t, d: None)
    gc._access.clear()
    return fake


TOKENS = {"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "scope": " ".join(("openid", "email", *gc.SCOPES))}
CLAIMS = {"sub": "111", "email": "ana@gmail.com"}


def connect(session, sessions=False):
    gc.connect(session, TOKENS, CLAIMS, NOW)
    if sessions:
        p = gc.prefs(session)
        gc._save_prefs(session, {**p, "sessions": True})
        session.commit()


def at(hours):
    return NOW + timedelta(hours=hours)


def rnd(session, pull=True):
    gc.run_round(session, NOW, pull)
    session.expire_all()
    return gc.prefs(session)


def google_event(fake, gid):
    return fake.calendars[CAL][gid]


# --- connecting ----------------------------------------------------------------------------------------------------

def test_connect_creates_the_peace_calendar_and_keeps_the_permission_private(fake, session, tmp_path):
    connect(session)
    p = gc.prefs(session)
    assert p["calendar_id"] == CAL and p["connected"] and p["email"] == "ana@gmail.com"
    assert datetime.fromisoformat(p["since"]) == datetime(2026, 10, 28, tzinfo=UTC)  # midnight Lisbon (UTC in winter)
    saved = tmp_path / "google.json"
    assert json.loads(saved.read_text())["refresh_token"] == "rt" and saved.stat().st_mode & 0o777 == 0o600


def test_connect_refuses_without_the_calendar_permission(fake, session):
    with pytest.raises(gc.GoogleError):
        gc.connect(session, {**TOKENS, "scope": "openid email"}, CLAIMS, NOW)
    with pytest.raises(gc.GoogleError):
        gc.connect(session, {**TOKENS, "refresh_token": None}, CLAIMS, NOW)


# --- PEACE -> Google -----------------------------------------------------------------------------------------------

def test_events_and_deadlines_are_pushed_once_and_kept_in_step(fake, session):
    connect(session)
    course = Course(name="Sistemas Operativos", code="SO")
    session.add(course)
    session.flush()
    ev = Event(title="Exame SO", kind="exam", course_id=course.id, starts_at=at(24), ends_at=at(26))
    old = Event(title="Ontem", kind="appointment", starts_at=NOW - timedelta(days=2), ends_at=NOW - timedelta(days=2))
    task = Task(title="Meta 2", due_at=at(48), course_id=course.id)
    session.add_all([ev, old, task, StudySession(starts_at=at(3), ends_at=at(4))])
    session.commit()
    rnd(session)
    g = google_event(fake, f"peacee{ev.id}")
    assert g["summary"] == "Exame SO" and g["colorId"] == "11"
    assert g["extendedProperties"]["private"] == {"peace": f"event:{ev.id}", "peace_kind": "exam"}
    d = google_event(fake, f"peacet{task.id}")
    assert d["summary"] == "📌 Entrega: Meta 2" and d["transparency"] == "transparent"
    assert d["end"]["dateTime"] == at(48).isoformat() and d["start"]["dateTime"] == at(47.5).isoformat()
    assert f"peacee{old.id}" not in fake.calendars[CAL]  # before the connection day
    assert not any(k.startswith("peaces") for k in fake.calendars[CAL])  # study sessions are off by default

    writes = len(fake.calls)
    rnd(session)
    assert [c for c in fake.calls[writes:] if c[0] in ("POST", "PUT", "DELETE")] == []  # nothing changed

    ev.starts_at, ev.ends_at = at(30), at(32)
    task.status = "COMPLETED"
    session.commit()
    rnd(session)
    assert google_event(fake, f"peacee{ev.id}")["start"]["dateTime"] == at(30).isoformat()
    assert google_event(fake, f"peacet{task.id}")["summary"] == "✅ Entrega: Meta 2"

    session.delete(ev)
    session.commit()
    rnd(session)
    assert google_event(fake, f"peacee{ev.id}")["status"] == "cancelled"
    assert session.scalar(select(GoogleLink).filter_by(entity="event", entity_id=ev.id)) is None


def test_study_sessions_sync_when_turned_on(fake, session):
    connect(session, sessions=True)
    task = Task(title="Ler capítulo 3")
    session.add(task)
    session.flush()
    s = StudySession(task_id=task.id, starts_at=at(3), ends_at=at(4))
    session.add(s)
    session.commit()
    rnd(session)
    assert google_event(fake, f"peaces{s.id}")["summary"] == "📚 Estudo: Ler capítulo 3"


def test_a_retry_after_a_crash_never_duplicates(fake, session):
    connect(session)
    ev = Event(title="Reunião", kind="appointment", starts_at=at(5), ends_at=at(6))
    session.add(ev)
    session.commit()
    # Google already has it (the first attempt's answer was lost before PEACE saved the link).
    fake.add(CAL, {"id": f"peacee{ev.id}", "summary": "Reunião", "start": {}, "end": {}})
    rnd(session, pull=False)
    assert list(fake.calendars[CAL]) == [f"peacee{ev.id}"]
    assert session.scalar(select(GoogleLink).filter_by(entity_id=ev.id)).etag == google_event(fake, f"peacee{ev.id}")["etag"]


def test_rate_limits_back_off_then_succeed(fake, session):
    connect(session)
    session.add(Event(title="A", kind="appointment", starts_at=at(5), ends_at=at(6)))
    session.commit()
    fake.fail_next = [429, 503]
    assert rnd(session, pull=False)["error"] is None
    assert len(fake.calendars[CAL]) == 1


def test_an_edit_made_in_google_meanwhile_is_not_overwritten(fake, session):
    connect(session)
    ev = Event(title="Reunião", kind="appointment", starts_at=at(5), ends_at=at(6))
    session.add(ev)
    session.commit()
    rnd(session)
    fake.edit(CAL, f"peacee{ev.id}", summary="Reunião (sala 3)")
    ev.title = "Reunião com a Ana"
    session.commit()
    rnd(session, pull=False)  # push only: If-Match fails, Google's text stays
    assert google_event(fake, f"peacee{ev.id}")["summary"] == "Reunião (sala 3)"


# --- Google -> PEACE -----------------------------------------------------------------------------------------------

def test_moving_and_deleting_in_google_reaches_peace(fake, session):
    connect(session)
    ev = Event(title="Reunião", kind="appointment", starts_at=at(5), ends_at=at(6))
    session.add(ev)
    session.commit()
    rnd(session)
    fake.edit(CAL, f"peacee{ev.id}", start={"dateTime": at(7).isoformat()}, end={"dateTime": at(8).isoformat()},
              summary="Reunião adiada")
    rnd(session)
    session.refresh(ev)
    assert (ev.title, ev.starts_at, ev.ends_at) == ("Reunião adiada", at(7), at(8))
    puts = [c for c in fake.calls if c[0] == "PUT"]
    rnd(session)
    assert [c for c in fake.calls if c[0] == "PUT"] == puts  # Google's change is not echoed back
    fake.edit(CAL, f"peacee{ev.id}", status="cancelled")
    rnd(session)
    assert session.get(Event, ev.id) is None


def test_deleting_an_imported_class_in_google_cancels_it_for_good(fake, session, client):
    connect(session)
    upsert_from_source(session, Event, "inforestudante", "T1-2026-10-29", {
        "kind": "class", "title": "SO T1", "starts_at": at(26), "ends_at": at(28), "location": "B1"})
    session.commit()
    ev = session.scalar(select(Event).filter_by(external_id="T1-2026-10-29"))
    rnd(session)
    fake.edit(CAL, f"peacee{ev.id}", status="cancelled")
    rnd(session)
    assert client.get("/api/v1/events", params={"start": NOW.isoformat(), "end": at(72).isoformat()}).json() == []
    # The university sync finds the cancelled row and leaves it cancelled.
    upsert_from_source(session, Event, "inforestudante", "T1-2026-10-29", {
        "kind": "class", "title": "SO T1", "starts_at": at(26), "ends_at": at(28), "location": "B2"})
    session.commit()
    rows = session.scalars(select(Event).execution_options(include_cancelled=True)).all()
    assert len(rows) == 1 and rows[0].cancelled and rows[0].location == "B2"


def test_deleting_a_deadline_in_google_keeps_the_task_and_does_not_recreate_it(fake, session):
    connect(session)
    task = Task(title="Meta 2", due_at=at(48))
    session.add(task)
    session.commit()
    rnd(session)
    fake.edit(CAL, f"peacet{task.id}", status="cancelled")
    rnd(session)
    rnd(session)
    assert session.get(Task, task.id).status == "TODO"
    assert google_event(fake, f"peacet{task.id}")["status"] == "cancelled"


def test_new_events_in_google_are_classified(fake, session):
    connect(session)
    session.add(Course(name="Sistemas Operativos", code="SO"))
    session.commit()
    rnd(session)
    fake.add(CAL, {"id": "abc", "summary": "Teste de SO", "description": "Anfiteatro", "start": {"dateTime": at(30).isoformat()},
                   "end": {"dateTime": at(32).isoformat()}})
    fake.add(CAL, {"id": "def", "summary": "Dentista", "start": {"dateTime": at(10).isoformat()}, "end": {"dateTime": at(11).isoformat()}})
    rnd(session)
    exam = session.scalar(select(Event).filter_by(title="Teste de SO"))
    assert exam.kind == "exam" and exam.course.code == "SO" and exam.source == "manual"
    assert session.scalar(select(Event).filter_by(title="Dentista")).kind == "appointment"
    assert session.scalar(select(Change).filter_by(entity_id=exam.id)).summary == "Novo exame: Teste de SO"
    rnd(session)
    assert google_event(fake, "abc")["description"] == "Anfiteatro"  # the user's text is untouched
    assert google_event(fake, "abc")["extendedProperties"]["private"]["peace"] == f"event:{exam.id}"


def test_one_refused_event_does_not_block_the_others(fake, session):
    connect(session)
    bad = Event(title="REJECT", kind="appointment", starts_at=at(5), ends_at=at(6))
    good = Event(title="Bom", kind="appointment", starts_at=at(7), ends_at=at(8))
    session.add_all([bad, good])
    session.commit()
    assert rnd(session, pull=False)["error"] == "google"
    assert f"peacee{good.id}" in fake.calendars[CAL] and f"peacee{bad.id}" not in fake.calendars[CAL]


def test_reconnecting_keeps_events_made_in_google_single(fake, session, client):
    connect(session)
    rnd(session)
    fake.add(CAL, {"id": "abc", "summary": "Dentista", "start": {"dateTime": at(10).isoformat()}, "end": {"dateTime": at(11).isoformat()}})
    rnd(session)
    rnd(session)
    client.delete("/api/v1/google/calendar")
    connect(session)
    rnd(session)
    rnd(session)
    assert [e.title for e in session.scalars(select(Event))] == ["Dentista"] and list(fake.calendars[CAL]) == ["abc"]


def test_a_reconnect_from_the_api_replaces_the_workers_token(fake, session, tmp_path):
    connect(session)
    gc._access.update(token="old", refresh="old-rt", until=10**12)
    assert gc.access_token() == "at"  # the saved permission changed: the cached token is not reused


def test_a_calendar_shared_without_access_is_skipped(fake, session):
    connect(session)
    fake.calendars["locked"] = {}
    fake.listed = [{"id": "locked", "selected": True}, {"id": "primary", "selected": True}]
    fake.add("primary", {"id": "x1", "summary": "Jantar", "start": {"dateTime": at(20).isoformat()}, "end": {"dateTime": at(21).isoformat()}})
    real = fake.__call__

    def guarded(method, url, **kw):
        return httpx.Response(403) if "/calendars/locked/" in url else real(method, url, **kw)
    gc._http = guarded
    assert rnd(session)["error"] is None
    assert session.scalar(select(Event).filter_by(title="Jantar")) is not None


def test_correcting_the_type_in_peace_is_stored_in_google(fake, session):
    connect(session)
    rnd(session)
    fake.add(CAL, {"id": "abc", "summary": "Apresentação", "start": {"dateTime": at(30).isoformat()}, "end": {"dateTime": at(31).isoformat()}})
    rnd(session)
    ev = session.scalar(select(Event).filter_by(title="Apresentação"))
    ev.kind = "exam"
    session.commit()
    rnd(session)
    assert google_event(fake, "abc")["extendedProperties"]["private"]["peace_kind"] == "exam"
    fake.edit(CAL, "abc", summary="Apresentação final")  # a later Google edit keeps the corrected type
    
    rnd(session)
    session.refresh(ev)
    assert (ev.title, ev.kind) == ("Apresentação final", "exam")


@pytest.mark.parametrize("peace_newer", [True, False])
def test_edited_on_both_sides_the_newest_edit_wins(fake, session, peace_newer):
    connect(session)
    ev = Event(title="Reunião", kind="appointment", starts_at=at(5), ends_at=at(6))
    session.add(ev)
    session.commit()
    rnd(session)
    fake.edit(CAL, f"peacee{ev.id}", summary="Versão Google")
    google_at = datetime.fromisoformat(google_event(fake, f"peacee{ev.id}")["updated"].replace("Z", "+00:00"))
    session.execute(update(Event).where(Event.id == ev.id).values(
        title="Versão PEACE", updated_at=google_at + timedelta(minutes=1 if peace_newer else -1)))
    session.commit()
    rnd(session)
    session.refresh(ev)
    winner = "Versão PEACE" if peace_newer else "Versão Google"
    assert ev.title == winner and google_event(fake, f"peacee{ev.id}")["summary"] == winner
    note = session.scalar(select(Change).where(Change.source == "google")).summary
    assert note.startswith("Editado nos dois lados, ficou a versão da PEACE" if peace_newer else "Editado nos dois lados, ficou a versão do Google")


def test_an_expired_sync_token_lists_everything_again(fake, session):
    connect(session)
    rnd(session)
    p = gc.prefs(session)
    gc._save_prefs(session, {**p, "sync_tokens": {CAL: "expired"}})
    session.commit()
    fake.add(CAL, {"id": "abc", "summary": "Aula SO", "start": {"dateTime": at(3).isoformat()}, "end": {"dateTime": at(4).isoformat()}})
    assert rnd(session)["error"] is None
    assert session.scalar(select(Event).filter_by(title="Aula SO")) is not None


# --- read-only calendars -------------------------------------------------------------------------------------------

def test_other_calendars_come_in_read_only_and_classified(fake, session, client):
    connect(session)
    fake.add("primary", {"id": "x1", "summary": "Exame de condução", "start": {"dateTime": at(20).isoformat()}, "end": {"dateTime": at(21).isoformat()}})
    fake.add("primary", {"id": "x2", "summary": "Lembrete", "transparency": "transparent", "start": {"dateTime": at(2).isoformat()}, "end": {"dateTime": at(3).isoformat()}})
    fake.add("primary", {"id": "x3", "summary": "Feriado", "start": {"date": "2026-10-30"}, "end": {"date": "2026-10-31"}})
    rnd(session)
    rows = {e.title: e for e in session.scalars(select(Event).where(Event.source == "google"))}
    assert set(rows) == {"Exame de condução", "Feriado"} and rows["Exame de condução"].kind == "exam"
    assert rows["Feriado"].starts_at == datetime(2026, 10, 30, tzinfo=UTC)
    assert not any(k.startswith("peacee") for k in fake.calendars[CAL])  # never copied into the PEACE calendar
    eid = rows["Exame de condução"].id
    assert client.patch(f"/api/v1/events/{eid}", json={"title": "x"}).status_code == 409
    assert client.delete(f"/api/v1/events/{eid}").status_code == 409
    assert client.patch(f"/api/v1/events/{eid}", json={"kind": "appointment"}).json()["kind"] == "appointment"
    fake.edit("primary", "x1", location="Centro de exames")
    rnd(session)
    assert session.get(Event, eid).kind == "appointment"  # the correction survives Google's later edit
    fake.listed = []
    rnd(session)
    assert session.scalars(select(Event).where(Event.source == "google")).all() == []


# --- errors, settings, disconnecting -------------------------------------------------------------------------------

def test_a_revoked_permission_asks_to_reconnect(fake, session, client):
    connect(session)
    gc._access.clear()
    fake.refresh_ok = False
    assert rnd(session)["error"] == "reconnect"
    assert client.get("/api/v1/google/calendar").json()["error"] == "reconnect"


def test_settings_toggle_and_disconnect_keeping_peace_data(fake, session, client, tmp_path):
    assert client.get("/api/v1/google/calendar").json()["connected"] is False
    assert client.put("/api/v1/google/calendar", json={"sessions": True}).status_code == 409
    connect(session)
    assert client.put("/api/v1/google/calendar", json={"sessions": True}).json()["sessions"] is True
    session.add(Event(title="Minha", kind="appointment", starts_at=at(5), ends_at=at(6)))
    session.commit()
    fake.add("primary", {"id": "x1", "summary": "Jantar", "start": {"dateTime": at(20).isoformat()}, "end": {"dateTime": at(21).isoformat()}})
    rnd(session)
    assert client.delete("/api/v1/google/calendar").status_code == 204
    session.expire_all()
    titles = [e.title for e in session.scalars(select(Event))]
    assert titles == ["Minha"] and not (tmp_path / "google.json").exists() and fake.revoked == ["rt"]
    assert CAL in fake.calendars and session.scalars(select(GoogleLink)).all() == []
    status = client.get("/api/v1/google/calendar").json()
    assert status["connected"] is False and status["sessions"] is True


def test_reconnecting_to_a_kept_calendar_links_peace_events_again(fake, session, client):
    connect(session)
    ev = Event(title="Minha", kind="appointment", starts_at=at(5), ends_at=at(6))
    session.add(ev)
    session.commit()
    rnd(session)
    client.delete("/api/v1/google/calendar")
    connect(session)
    rnd(session)
    rnd(session)
    assert list(fake.calendars[CAL]) == [f"peacee{ev.id}"] and len(session.scalars(select(Event)).all()) == 1


def test_disconnect_can_delete_the_peace_calendar(fake, session, client):
    connect(session)
    assert client.delete("/api/v1/google/calendar", params={"delete_calendar": True}).status_code == 204
    assert CAL not in fake.calendars


def test_deleting_an_imported_class_in_peace_cancels_it(session, client):
    upsert_from_source(session, Event, "inforestudante", "T1", {
        "kind": "class", "title": "SO T1", "starts_at": at(26), "ends_at": at(28), "location": None})
    session.commit()
    ev = session.scalar(select(Event))
    assert client.delete(f"/api/v1/events/{ev.id}").status_code == 204
    session.expire_all()
    assert session.scalar(select(Event).execution_options(include_cancelled=True)).cancelled


# --- the consent step ----------------------------------------------------------------------------------------------

def test_calendar_consent_needs_a_signed_in_browser_and_asks_for_offline_access(fake, monkeypatch):
    browser = TestClient(api.app, base_url="http://127.0.0.1:8421", follow_redirects=False)
    assert browser.get("/auth/google/start", params={"calendar": 1}).headers["location"] == "/entrar?erro=primeiro"
    browser.post("/auth/login", json={"token": TOKEN})
    r = browser.get("/auth/google/start", params={"calendar": 1})
    q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    assert set(gc.SCOPES) <= set(q["scope"].split()) and q["access_type"] == "offline" and q["prompt"] == "consent"

    def exchange(code, verifier, redirect_uri):
        import base64
        claims = {"iss": "https://accounts.google.com", "aud": "cid", "sub": "111", "email": "ana@gmail.com",
                  "email_verified": True, "exp": NOW.timestamp() + 10**9, "nonce": q["nonce"]}
        return {**TOKENS, "id_token": "e30." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".s"}
    monkeypatch.setattr(google, "exchange", exchange)
    r = browser.get("/auth/google/callback", params={"code": "c", "state": q["state"]})
    assert r.headers["location"] == "/definicoes?google=ligado"
    assert browser.get("/api/v1/google/calendar").json()["connected"] is True

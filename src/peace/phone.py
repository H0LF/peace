"""Reminders on the phone through ntfy (https://ntfy.sh): the student installs the ntfy app and subscribes to a
secret topic PEACE made up. The worker sends what the PC would show, with the same quiet hours and rules.

Messages go through the ntfy server (ntfy.sh unless another one is set): the topic name is the only secret, so it
is long and random, and Settings says so.
"""

import logging
import secrets
from datetime import datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from peace import routes
from peace.auth import require_token
from peace.db import get_session
from peace.models import Preference

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
DESKTOP_AWAY = timedelta(seconds=90)  # the agent's heartbeat is every 30 s


class PhonePrefs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    server: str = Field("https://ntfy.sh", max_length=200, pattern=r"^https://[^\s/?#]+(/[^\s?#]*)?$")
    topic: str = Field("", pattern=r"^(peace-[A-Za-z0-9_-]{20,64})?$")
    mode: Literal["away", "always"] = "away"  # away: only while the PC agent is off (then it counts as delivered)


def phone_prefs(session: Session) -> PhonePrefs:
    """Saved settings; the secret topic is made the first time they are read."""
    row = session.get(Preference, "phone")
    prefs = PhonePrefs.model_validate(row.value) if row else PhonePrefs()
    if not prefs.topic:
        prefs = prefs.model_copy(update={"topic": "peace-" + secrets.token_urlsafe(18)})
        if row:
            row.value = prefs.model_dump()
        else:
            session.add(Preference(key="phone", value=prefs.model_dump()))
        session.commit()
    return prefs


def desktop_connected(session: Session, now: datetime) -> bool:
    row = session.get(Preference, "desktop")
    seen = row.value.get("seen_at") if row else None
    return bool(seen and now - datetime.fromisoformat(seen) < DESKTOP_AWAY)


def send(prefs: PhonePrefs, title: str, message: str, urgent: bool = False) -> None:
    """One push. JSON publishing keeps accents intact (header-based publishing is ASCII-only)."""
    r = httpx.post(prefs.server.rstrip("/"), timeout=10, json={
        "topic": prefs.topic, "title": title[:200], "message": message[:3000] or title,
        "priority": 4 if urgent else 3, "tags": ["books"]})
    r.raise_for_status()


def push_due(session: Session, now: datetime) -> int:
    """Called by the worker every minute. Returns how many were sent."""
    prefs = phone_prefs(session)
    if not prefs.enabled or (prefs.mode == "away" and desktop_connected(session, now)):
        return 0
    sent = 0
    for n in routes.due_notifications(session, now):
        if n.pushed_at is not None or n.kind == "test":
            continue
        try:
            send(prefs, n.title, n.body, n.urgent)
        except httpx.HTTPError as e:
            log.warning("phone push failed: %s", type(e).__name__)
            break  # the server is unreachable: the next tick tries again
        n.pushed_at = now
        if prefs.mode == "away":
            n.delivered_at = now  # shown on the phone: the PC does not repeat it later
        session.commit()
        sent += 1
    return sent


@router.get("/preferences/phone", response_model=PhonePrefs)
def get_phone(session: Session = Depends(get_session)):
    return phone_prefs(session)


@router.put("/preferences/phone", response_model=PhonePrefs)
def put_phone(body: PhonePrefs, session: Session = Depends(get_session)):
    current = phone_prefs(session)
    value = body.model_copy(update={"topic": body.topic or current.topic})
    session.get(Preference, "phone").value = value.model_dump()
    routes._audit(session, "preferences.phone", enabled=value.enabled, server=value.server, mode=value.mode)
    session.commit()
    return value


@router.post("/phone/test", status_code=204)
def test_phone(session: Session = Depends(get_session)):
    prefs = phone_prefs(session)
    try:
        send(prefs, "PEACE", "Teste: se vês isto no telemóvel, os lembretes vão chegar aqui.")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"O servidor ntfy não aceitou a mensagem ({type(e).__name__}).") from e

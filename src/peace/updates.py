"""Knowing about new versions of PEACE and asking for them. Separate from academic data: syncing never touches the code.

A server cannot replace itself, so PEACE only decides here; scripts/update.sh or update.ps1, on the host, asks every few
minutes whether to install, then downloads, verifies (peace.release), backs up, swaps, and rolls back if PEACE does not
come back healthy. Only published, signed releases are offered; a development build sees nothing.

PEACE_INSTALL_KIND says who manages this copy: "docker" or "linux" (the installers set it), or unset: someone manages it
by hand, or a package manager does, and PEACE only shows its version.
"""

import logging
import os
import re
from datetime import datetime, timedelta
from importlib import metadata
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from peace import routes
from peace.auth import require_token
from peace.db import get_session
from peace.models import Preference
from peace.release import VERSION

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/updates", dependencies=[Depends(require_token)])
DB = Depends(get_session)
NOW = Depends(routes.get_now)
REPO = os.environ.get("PEACE_UPDATE_REPO", "H0LF/peace")
CHECK_EVERY = timedelta(hours=24)
STUCK = timedelta(hours=1)  # an install that never reported back: the host may have been switched off


def current_version() -> str:
    try:
        return metadata.version("peace")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def install_kind() -> str | None:
    kind = os.environ.get("PEACE_INSTALL_KIND")
    return kind if kind in ("docker", "linux") else None


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in version.split("."))


def _state(session: Session) -> dict[str, Any]:
    row = session.get(Preference, "updates")
    return dict(row.value) if row else {}


def _save(session: Session, value: dict[str, Any]) -> None:
    row = session.get(Preference, "updates")
    if row:
        row.value = value
    else:
        session.add(Preference(key="updates", value=value))
    session.commit()


def latest_release() -> dict[str, Any] | None:
    """The newest published stable release that carries a signed bundle. GitHub's "latest" never is a draft or a
    prerelease, so work in progress is never offered."""
    r = httpx.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=15, headers={"Accept": "application/vnd.github+json"})
    if r.status_code == 404:
        return None  # nothing published yet
    r.raise_for_status()
    data = r.json()
    version = str(data.get("tag_name", "")).removeprefix("v")
    if not VERSION.match(version) or data.get("draft") or data.get("prerelease"):
        return None
    assets = {a.get("name"): a.get("browser_download_url", "") for a in data.get("assets", [])}
    bundle, signature = assets.get(f"peace-{version}.tar.gz"), assets.get(f"peace-{version}.tar.gz.sig")
    prefix = f"https://github.com/{REPO}/releases/download/"
    if not (bundle and signature and bundle.startswith(prefix) and signature.startswith(prefix)):
        return None
    return {"version": version, "notes": str(data.get("body") or "")[:5000], "bundle": bundle, "signature": signature}


def check(session: Session, now: datetime) -> dict[str, Any]:
    """Ask GitHub. A failure keeps what was known and says so; it never blocks anything else."""
    state = _state(session)
    try:
        latest = latest_release()
        state |= {"latest": latest, "check_error": None}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as e:
        log.warning("update check failed: %s", type(e).__name__)
        state |= {"check_error": "network"}
    state["checked_at"] = now.isoformat()
    _save(session, state)
    return state


def newer(state: dict[str, Any]) -> dict[str, Any] | None:
    latest = state.get("latest")
    return latest if latest and _key(latest["version"]) > _key(current_version()) else None


def daily_check(session: Session, now: datetime) -> bool:
    """Worker. Only for copies PEACE can update; the student can turn checking off."""
    state = _state(session)
    if not install_kind() or state.get("policy") == "off":
        return False
    last = state.get("checked_at")
    if last and now - datetime.fromisoformat(last) < CHECK_EVERY:
        return False
    check(session, now)
    return True


def view(session: Session, now: datetime) -> dict[str, Any]:
    state = _state(session)
    available = newer(state)
    status = state.get("status", "idle")
    if status in ("requested", "installing") and state.get("status_at") and now - datetime.fromisoformat(state["status_at"]) > STUCK:
        status, state["message"] = "failed", "no_report"
    return {"version": current_version(), "kind": install_kind(), "policy": state.get("policy", "notify"), "available": available,
            "checked_at": state.get("checked_at"), "check_error": state.get("check_error"), "status": status, "message": state.get("message")}


class PolicyIn(BaseModel):
    policy: Literal["auto", "notify", "off"]


class ReportIn(BaseModel):
    status: Literal["installing", "done", "failed"]
    message: str = Field("", max_length=300)


@router.get("")
def get_updates(session: Session = DB, now: datetime = NOW):
    return view(session, now)


@router.post("/check")
def post_check(session: Session = DB, now: datetime = NOW):
    if not install_kind():
        raise HTTPException(409, "managed_elsewhere")
    check(session, now)
    return view(session, now)


@router.put("/policy")
def put_policy(body: PolicyIn, session: Session = DB, now: datetime = NOW):
    _save(session, _state(session) | {"policy": body.policy})
    return view(session, now)


@router.post("/install")
def post_install(session: Session = DB, now: datetime = NOW):
    """The student pressed Update: the host's script picks it up within minutes."""
    state = _state(session)
    if not install_kind():
        raise HTTPException(409, "managed_elsewhere")
    if not newer(state):
        raise HTTPException(409, "up_to_date")
    _save(session, state | {"status": "requested", "status_at": now.isoformat(), "message": None})
    return view(session, now)


@router.get("/pending")
def get_pending(session: Session = DB, now: datetime = NOW):
    """Asked by the host's script: install now, and what? With policy "auto" a new version is enough; after a failure,
    only the student pressing Update tries again, so a bad release cannot loop."""
    state = _state(session)
    available = newer(state)
    if not available or not install_kind():
        return {"install": False}
    wanted = state.get("status") == "requested" or (state.get("policy") == "auto" and state.get("failed_version") != available["version"])
    return {"install": wanted, **available} if wanted else {"install": False}


@router.post("/report", status_code=204)
def post_report(body: ReportIn, session: Session = DB, now: datetime = NOW):
    state = _state(session) | {"status": body.status, "status_at": now.isoformat(), "message": re.sub(r"\s+", " ", body.message) or None}
    available = newer(state)
    if body.status == "failed" and available:
        state["failed_version"] = available["version"]
    if body.status == "done":
        state.pop("failed_version", None)
    _save(session, state)

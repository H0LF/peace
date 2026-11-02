"""Sign in with Google through one OAuth client that ships with PEACE, so nobody has to open Google Cloud.

The client is Google's "Desktop app" type: authorization code + PKCE, redirected to this PC's loopback address on any
port. Google documents that a desktop client's secret is not confidential, so it ships in the code like the id.
The ID token comes straight from Google's token endpoint over TLS, so its claims are checked but not its signature
(Google's OpenID Connect guide allows this); no JWT library needed.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from peace import routes
from peace.auth import require_token, session_cookie, start_session
from peace.config import get_settings
from peace.db import get_session
from peace.models import Preference

# Filled in once by PEACE's maker (docs/operations/GOOGLE_SETUP.md); the environment overrides them for development.
SHIPPED_CLIENT_ID = ""
SHIPPED_CLIENT_SECRET = ""
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}  # the only http redirect Google accepts
FLOW_SECONDS = 600

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/google")


def client() -> tuple[str, str]:
    return (os.environ.get("PEACE_GOOGLE_CLIENT_ID", SHIPPED_CLIENT_ID),
            os.environ.get("PEACE_GOOGLE_CLIENT_SECRET", SHIPPED_CLIENT_SECRET))


def _flow_cookie() -> str:
    return session_cookie() + "_google"  # one per PEACE, like the session cookie


def _mac(raw: str) -> str:
    return hmac.new(get_settings().api_token.encode(), b"peace-google-flow:" + raw.encode(), hashlib.sha256).hexdigest()


def _sign(flow: dict[str, Any]) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(flow).encode()).decode()
    return f"{raw}.{_mac(raw)}"


def _eq(a: Any, b: str) -> bool:
    return isinstance(a, str) and hmac.compare_digest(a.encode(), b.encode())  # bytes: str compare rejects non-ASCII


def _unsign(value: str | None) -> dict[str, Any] | None:
    raw, _, mac = (value or "").partition(".")
    if not raw or not _eq(mac, _mac(raw)):
        return None
    try:
        flow = json.loads(base64.urlsafe_b64decode(raw))
        return flow if time.time() - flow["at"] < FLOW_SECONDS else None
    except (ValueError, KeyError, TypeError):
        return None


def _redirect_uri(request: Request) -> str:
    """From the validated host and port only (never the raw Host header)."""
    host = request.url.hostname or ""
    return f"http://{f'[{host}]' if ':' in host else host}:{request.url.port or 80}/auth/google/callback"


def _signed_in(request: Request) -> bool:
    try:
        require_token(request, None)
        return True
    except HTTPException:
        return False


def _fail(reason: str, page: str = "/entrar?erro=") -> RedirectResponse:
    response = RedirectResponse(f"{page}{reason}", 303)
    response.delete_cookie(_flow_cookie(), path="/auth/google")
    return response


def claims_from(id_token: str) -> dict[str, Any]:
    payload = id_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def exchange(code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
    """Trade the authorization code for Google's tokens (ID token; for Calendar also access and refresh tokens)."""
    client_id, secret = client()
    r = httpx.post(TOKEN_URL, timeout=15, data={
        "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        "client_id": client_id, "client_secret": secret,
    })
    r.raise_for_status()
    return r.json()


def _trusted(claims: Any, nonce: str) -> bool:
    try:
        return (claims.get("iss") in ISSUERS and claims.get("aud") == client()[0]
                and float(claims.get("exp", 0)) > time.time() and claims.get("email_verified") is True
                and _eq(claims.get("nonce"), nonce) and isinstance(claims.get("sub"), str))
    except (AttributeError, TypeError, ValueError):
        return False


@router.get("")
def status():
    """Whether the login page can offer Google (a build without the shipped client cannot)."""
    return {"available": bool(client()[0])}


@router.get("/account", dependencies=[Depends(require_token)])
def account(session: Session = Depends(get_session)):
    """The Google account that owns this PEACE (Settings shows it, or offers to link one)."""
    owner = session.get(Preference, "owner")
    return {"available": bool(client()[0]), "email": owner.value.get("email") if owner else None}


@router.get("/start")
def start(request: Request, calendar: bool = False):
    """Sign in, or (calendar=1, from Settings) also grant the Calendar permission."""
    from peace.google_calendar import SCOPES

    client_id = client()[0]
    if not client_id:
        return _fail("indisponivel")
    if request.url.hostname not in LOOPBACK:
        return _fail("endereco")
    if calendar and not _signed_in(request):
        return _fail("primeiro")
    flow = {"state": secrets.token_urlsafe(24), "verifier": secrets.token_urlsafe(48),
            "nonce": secrets.token_urlsafe(24), "at": time.time(),
            # Only a browser already holding the token (the installer's link) may make its Google account the owner.
            "bind": _signed_in(request), "calendar": calendar}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest()).rstrip(b"=").decode()
    query = {"client_id": client_id, "redirect_uri": _redirect_uri(request), "response_type": "code",
             "scope": "openid email", "state": flow["state"], "nonce": flow["nonce"], "code_challenge": challenge,
             "code_challenge_method": "S256", "prompt": "select_account"}
    if calendar:
        # offline + consent: Google returns a refresh token, so the worker keeps syncing without the browser.
        query.update(scope=" ".join(("openid", "email", *SCOPES)), access_type="offline", prompt="consent",
                     include_granted_scopes="true")
    response = RedirectResponse(f"{AUTH_URL}?{urlencode(query)}", 303)
    # Lax, not strict: Google's redirect back is a cross-site navigation and must carry this cookie.
    response.set_cookie(_flow_cookie(), _sign(flow), max_age=FLOW_SECONDS, httponly=True, samesite="lax",
                        path="/auth/google", secure=request.url.scheme == "https")
    return response


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = "",
             session: Session = Depends(get_session)):
    if error:
        return _fail("cancelado" if error == "access_denied" else "google")
    flow = _unsign(request.cookies.get(_flow_cookie()))
    if not flow or not code or not _eq(state, flow["state"]):
        return _fail("pedido")
    try:
        tokens = exchange(code, flow["verifier"], _redirect_uri(request))
        claims = claims_from(tokens["id_token"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
        log.warning("Google sign-in failed: %s", e)
        return _fail("ligacao")
    if not _trusted(claims, flow["nonce"]):
        log.warning("Google sign-in refused: untrustworthy ID token")
        return _fail("pedido")
    owner = session.get(Preference, "owner")
    if owner is None:
        if not flow.get("bind"):
            return _fail("primeiro")
        try:
            session.add(Preference(key="owner", value={"sub": claims["sub"], "email": claims.get("email", "")}))
            routes._audit(session, "auth.owner", email=claims.get("email", ""))
            session.commit()
        except IntegrityError:  # another sign-in bound an owner a moment ago
            session.rollback()
            owner = session.get(Preference, "owner")
    if owner is not None and owner.value.get("sub") != claims["sub"]:
        log.warning("Google sign-in refused: not the owner's account")
        return _fail("conta")
    target = "/"
    if flow.get("calendar"):
        from peace import google_calendar

        try:
            google_calendar.connect(session, tokens, claims, datetime.now(UTC))
        except (google_calendar.GoogleError, httpx.HTTPError) as e:
            log.warning("Google Calendar not connected: %s", e)
            session.rollback()
            return _fail("permissao" if "permission" in str(e) else "erro", "/definicoes?google=")
        target = "/definicoes?google=ligado"
    response = RedirectResponse(target, 303)
    start_session(response, request)
    response.delete_cookie(_flow_cookie(), path="/auth/google")
    return response

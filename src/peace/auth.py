import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from peace.config import get_settings

_bearer = HTTPBearer(auto_error=False)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def session_value(token: str) -> str:
    """Dashboard cookie value. Derived from the API token, so rotating the token ends every session."""
    return hmac.new(token.encode(), b"peace-dashboard-session-v1", hashlib.sha256).hexdigest()


def session_cookie() -> str:
    """The cookie's name, one per PEACE: browsers share cookies across ports, so two PEACEs on one host (8420 and
    8421) with the same name would log each other out."""
    return "peace_session_" + hashlib.sha256(b"peace-cookie-name:" + get_settings().api_token.encode()).hexdigest()[:8]


def start_session(response: Response, request: Request) -> None:
    """Log this browser in (token or Google sign-in): a strict, http-only cookie for 30 days."""
    response.set_cookie(session_cookie(), session_value(get_settings().api_token), max_age=60 * 60 * 24 * 30,
                        httponly=True, samesite="strict", secure=request.url.scheme == "https")


def _same(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


def require_token(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    expected = get_settings().api_token
    if credentials is not None and _same(credentials.credentials, expected):
        return
    cookie = request.cookies.get(session_cookie())
    if cookie is not None and _same(cookie, session_value(expected)):
        # SameSite ignores ports: a page on another 127.0.0.1 port is "same-site" and would send the cookie.
        if request.method not in SAFE_METHODS and request.headers.get("sec-fetch-site") in ("same-site", "cross-site"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-origin request refused")
        return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token", headers={"WWW-Authenticate": "Bearer"})

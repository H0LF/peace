import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from peace.config import get_settings

SESSION_COOKIE = "peace_session"
_bearer = HTTPBearer(auto_error=False)


def session_value(token: str) -> str:
    """Dashboard cookie value. Derived from the API token, so rotating the token ends every session."""
    return hmac.new(token.encode(), b"peace-dashboard-session-v1", hashlib.sha256).hexdigest()


def _same(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


def require_token(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    expected = get_settings().api_token
    if credentials is not None and _same(credentials.credentials, expected):
        return
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie is not None and _same(cookie, session_value(expected)):
        return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token", headers={"WWW-Authenticate": "Bearer"})

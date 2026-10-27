import logging
import os
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from peace.auth import SESSION_COOKIE, _same, require_token, session_value
from peace.config import get_settings
from peace.db import get_session
from peace.logs import configure_logging
from peace import assistant, desktop
from peace.routes import router

log = logging.getLogger(__name__)
DASHBOARD = Path(os.environ.get("PEACE_DASHBOARD_DIR", Path(__file__).parents[2] / "dashboard" / "dist")).resolve()

app = FastAPI(title="PEACE")
app.include_router(router)
app.include_router(assistant.router)
app.include_router(desktop.router)


@app.get("/health")
def health(session: Session = Depends(get_session)):
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        log.exception("health check: database unreachable")
        return JSONResponse({"status": "degraded", "database": "down"}, status_code=503)
    return {"status": "ok", "database": "ok"}


class LoginIn(BaseModel):
    token: str = Field(min_length=1, max_length=500)


@app.post("/auth/login", status_code=204)
def login(body: LoginIn, request: Request):
    expected = get_settings().api_token
    if not _same(body.token, expected):
        raise HTTPException(401, "Token inválido")
    response = Response(status_code=204)
    response.set_cookie(
        SESSION_COOKIE,
        session_value(expected),
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return response


@app.post("/auth/logout", status_code=204)
def logout():
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/auth/session", status_code=204, dependencies=[Depends(require_token)])
def session_check():
    return Response(status_code=204)


@app.get("/{path:path}", include_in_schema=False)
def dashboard(path: str):
    """The built dashboard (single-page app): real files when they exist, index.html otherwise."""
    if path.startswith(("api/", "auth/")):
        raise HTTPException(404)
    target = (DASHBOARD / path).resolve()
    if path and target.is_file() and target.is_relative_to(DASHBOARD):
        return FileResponse(target)
    index = DASHBOARD / "index.html"
    if not index.is_file():
        raise HTTPException(404, "dashboard not built")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    # log_config=None lets uvicorn's loggers flow through the JSON handler.
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)

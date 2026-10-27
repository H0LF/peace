"""Client for the local language model (Ollama on the desktop GPU), shared by the assistant and the worker."""

import os
import time
from typing import Any

import httpx

# Ollama on this computer (PEACE_LLM_URL), or a forwarded Unix socket (PEACE_LLM_SOCKET) when PEACE runs elsewhere;
# through a socket the Host header must still read 127.0.0.1:11434 or Ollama answers 403.
LLM_URL = os.environ.get("PEACE_LLM_URL", "http://127.0.0.1:11434").rstrip("/")
LLM_SOCKET = os.environ.get("PEACE_LLM_SOCKET")
# Models that can pick PEACE's commands (tool calling), best first. The installer pulls the largest this PC runs.
PREFERRED = ("gpt-oss:20b", "qwen3:8b", "qwen3:4b", "llama3.2:3b")
LLM_TIMEOUT = float(os.environ.get("PEACE_LLM_TIMEOUT", "45"))  # a cold load of a 20B model takes ~11 s on a 16 GB GPU
# One context size for every caller: Ollama reloads the model (~11 s) whenever it changes.
LLM_OPTIONS = {"temperature": 0, "num_ctx": 8192}


def _llm(method: str, path: str, **kw: Any) -> httpx.Response:
    transport = httpx.HTTPTransport(uds=LLM_SOCKET) if LLM_SOCKET else None
    with httpx.Client(transport=transport, base_url=LLM_URL) as client:
        return client.request(method, path, **kw)


_reachable: dict[str, Any] = {"at": -60.0, "ok": False, "model": None, "installed": []}


def wanted_model() -> str:
    """The model chosen in Settings (or PEACE_LLM_MODEL); empty means the best one installed."""
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session

    from peace.db import get_engine
    from peace.models import Preference

    try:
        with Session(get_engine()) as session:
            row = session.get(Preference, "assistant")
    except (SQLAlchemyError, KeyError, OSError):  # no database configured or reachable: the environment decides
        row = None
    return (row.value.get("model", "") if row else "") or os.environ.get("PEACE_LLM_MODEL", "")


def pick_model(installed: list[str], wanted: str) -> str | None:
    if wanted:
        return wanted if wanted in installed else None
    return next((m for m in PREFERRED if m in installed), None)


def current_model() -> str:
    """The model in use (as of the last check); the first preferred one when nothing is known yet."""
    return _reachable["model"] or wanted_model() or PREFERRED[0]


def installed_models() -> list[str]:
    llm_reachable()
    return _reachable["installed"]


def llm_reachable() -> bool:
    """Is a usable model being served right now? Cached for 30 s so status polling stays cheap."""
    if time.monotonic() - _reachable["at"] < 30:
        return _reachable["ok"]
    try:
        tags = _llm("GET", "/api/tags", timeout=1.5).json()
        installed = [m["name"] for m in tags.get("models", []) if isinstance(m.get("name"), str)]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        installed = []
    chosen = pick_model(installed, wanted_model())
    _reachable.update(at=time.monotonic(), ok=chosen is not None, model=chosen, installed=installed)
    return chosen is not None


def forget_model_check() -> None:
    """The choice changed in Settings: check again on the next call."""
    _reachable["at"] = -60.0


def mark_unreachable() -> None:
    """A call just failed: report offline until the next check."""
    _reachable.update(at=time.monotonic(), ok=False)

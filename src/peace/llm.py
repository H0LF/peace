"""Client for the local language model (Ollama on the desktop GPU), shared by the assistant and the worker."""

import os
import time
from typing import Any

import httpx

# Ollama on this computer (PEACE_LLM_URL), or a forwarded Unix socket (PEACE_LLM_SOCKET) when PEACE runs elsewhere;
# through a socket the Host header must still read 127.0.0.1:11434 or Ollama answers 403.
LLM_URL = os.environ.get("PEACE_LLM_URL", "http://127.0.0.1:11434").rstrip("/")
LLM_SOCKET = os.environ.get("PEACE_LLM_SOCKET")
LLM_MODEL = os.environ.get("PEACE_LLM_MODEL", "gpt-oss:20b")
LLM_TIMEOUT = float(os.environ.get("PEACE_LLM_TIMEOUT", "45"))  # a cold load of a 20B model takes ~11 s on a 16 GB GPU
# One context size for every caller: Ollama reloads the model (~11 s) whenever it changes.
LLM_OPTIONS = {"temperature": 0, "num_ctx": 8192}


def _llm(method: str, path: str, **kw: Any) -> httpx.Response:
    transport = httpx.HTTPTransport(uds=LLM_SOCKET) if LLM_SOCKET else None
    with httpx.Client(transport=transport, base_url=LLM_URL) as client:
        return client.request(method, path, **kw)


_reachable: dict[str, Any] = {"at": -60.0, "ok": False}


def llm_reachable() -> bool:
    """Is the model being served right now? Cached for 30 s so status polling stays cheap."""
    if time.monotonic() - _reachable["at"] < 30:
        return _reachable["ok"]
    try:
        tags = _llm("GET", "/api/tags", timeout=1.5).json()
        ok = any(m.get("name") == LLM_MODEL for m in tags.get("models", []))
    except (httpx.HTTPError, ValueError):
        ok = False
    _reachable.update(at=time.monotonic(), ok=ok)
    return ok


def mark_unreachable() -> None:
    """A call just failed: report offline until the next check."""
    _reachable.update(at=time.monotonic(), ok=False)

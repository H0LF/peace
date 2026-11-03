"""A ChatGPT subscription as a provider (ADR-030): OpenAI's device login and the Codex backend, no Codex CLI needed.

This is not a public API. It uses the public client id of OpenAI's Codex, the same way other open-source agents do, and
says it is PEACE in every request. It can stop working if OpenAI changes it, so it is one choice among the others and
nothing connects to it unless the student logs in. Only plain HTTP: no agent runs and no files are touched.
Refresh tokens are single-use and reuse revokes the login, so PEACE keeps its own login, never ~/.codex/auth.json, and
one lock covers the API and the worker. No failure here carries a token: errors keep only a code.
"""

import base64
import json
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import httpx

from peace import providers
from peace.library import data_dir
from peace.providers import ProviderError

ID = "chatgpt_plan"
ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
BASE_URL = "https://chatgpt.com/backend-api/codex"
LOGIN_URL = f"{ISSUER}/codex/device"
LOGIN_SECONDS = 15 * 60
CLIENT_VERSIONS = ("99.0.0", "0.0.0")  # the model list is gated by client version; the second one is ungated
_transport: httpx.BaseTransport | None = None  # tests replace the network here
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_pending: dict[str, Any] = {}
_usage: dict[str, Any] = {}


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(transport=_transport, timeout=timeout)


def _claims(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


def _fresh(token: str) -> bool:
    exp = _claims(token).get("exp")
    return isinstance(exp, (int, float)) and exp - 60 > time.time()


def _stored() -> dict[str, str] | None:
    raw = providers.get_key(ID)
    try:
        tokens = json.loads(raw) if raw else None
    except ValueError:
        return None
    return tokens if isinstance(tokens, dict) and tokens.get("access") else None


def _save(tokens: dict[str, Any], old_refresh: str = "") -> None:
    _usage.clear()
    providers.save_key(ID, json.dumps({"access": tokens["access_token"], "refresh": tokens.get("refresh_token") or old_refresh}))


# --- login ---------------------------------------------------------------------------------------------------------

def start_login() -> dict[str, Any]:
    """Ask OpenAI for a code the student types on its page."""
    try:
        with _client(20) as c:
            r = c.post(f"{ISSUER}/api/accounts/deviceauth/usercode", json={"client_id": CLIENT_ID})
        if r.status_code == 429:
            raise ProviderError("rate")
        if r.status_code != 200:
            raise ProviderError("refused")
        data = r.json()
        code, device = data.get("user_code") or data["usercode"], data["device_auth_id"]
        interval = max(3, int(data.get("interval") or 5))
    except httpx.HTTPError as e:
        raise ProviderError("network") from e
    except (ValueError, KeyError, TypeError) as e:
        raise ProviderError("refused") from e
    _pending.update(code=code, device=device, until=time.monotonic() + LOGIN_SECONDS)
    return {"url": LOGIN_URL, "code": code, "interval": interval}


def poll_login() -> bool:
    """True once the student finished on OpenAI's page. A network blip only means "not yet"."""
    if not _pending or time.monotonic() > _pending["until"]:
        _pending.clear()
        raise ProviderError("expired")
    try:
        with _client(20) as c:
            r = c.post(f"{ISSUER}/api/accounts/deviceauth/token", json={"device_auth_id": _pending["device"], "user_code": _pending["code"]})
            if r.status_code in (403, 404):
                return False
            if r.status_code != 200:
                _pending.clear()
                raise ProviderError("refused")
            done = r.json()
            t = c.post(f"{ISSUER}/oauth/token", data={
                "grant_type": "authorization_code", "code": done["authorization_code"], "code_verifier": done["code_verifier"],
                "redirect_uri": f"{ISSUER}/deviceauth/callback", "client_id": CLIENT_ID})
        if t.status_code != 200:
            raise ProviderError("refused")
        tokens = t.json()
        if not tokens.get("access_token"):
            raise ProviderError("refused")
    except httpx.HTTPError:
        return False
    except (ValueError, KeyError, TypeError) as e:
        _pending.clear()
        raise ProviderError("refused") from e
    _pending.clear()
    _save(tokens)
    return True


def _access_token() -> str:
    """A valid token, refreshed under a lock: of two processes, the second finds the first's new token."""
    import fcntl  # shortcut: POSIX only, which is every way PEACE runs (Docker on Windows); use msvcrt if that changes

    tokens = _stored()
    if not tokens:
        raise ProviderError("login")
    if _fresh(tokens["access"]):
        return tokens["access"]
    path = data_dir() / "chatgpt.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        tokens = _stored()
        if not tokens or not tokens.get("refresh"):
            raise ProviderError("login")
        if _fresh(tokens["access"]):
            return tokens["access"]
        try:
            with _client(20) as c:
                r = c.post(f"{ISSUER}/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh"], "client_id": CLIENT_ID})
        except httpx.HTTPError as e:
            raise ProviderError("network") from e
        if r.status_code == 429:
            raise ProviderError("rate")
        if r.status_code >= 500:
            raise ProviderError("network")
        if r.status_code != 200:
            raise ProviderError("login")
        try:
            fresh = r.json()
            _save(fresh, tokens["refresh"])
            return fresh["access_token"]
        except (ValueError, KeyError, TypeError) as e:
            raise ProviderError("refused") from e


def _headers(token: str) -> dict[str, str]:
    auth = _claims(token).get("https://api.openai.com/auth")
    auth = auth if isinstance(auth, dict) else {}
    out = {"Authorization": f"Bearer {token}", "User-Agent": "PEACE", "originator": "peace"}
    if auth.get("chatgpt_account_id"):
        out["ChatGPT-Account-ID"] = auth["chatgpt_account_id"]  # without it the backend lists no models
    if residency := auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency"):
        out["x-openai-internal-codex-residency"] = residency
    return out


def _code(r: httpx.Response) -> str:
    if r.status_code in (401, 403):
        return "login"
    if r.status_code == 429 and "usage_limit" in r.text:
        return "quota"
    return providers._code(r)


def usage() -> dict[str, Any]:
    """How much of the plan is left, per window (5 hours, a week). Kept a minute: the main page asks often."""
    if _usage and time.monotonic() < _usage["until"]:
        return _usage["value"]
    headers = _headers(_access_token())
    try:
        with _client(15) as c:
            r = c.get(USAGE_URL, headers=headers)
        if r.status_code >= 400:
            raise ProviderError(_code(r))
        data = r.json()
        limits = data.get("rate_limit") or {}
        windows = [{"seconds": w["limit_window_seconds"], "used": w["used_percent"], "resets_at": datetime.fromtimestamp(w["reset_at"], UTC).isoformat()}
                   for w in (limits.get("primary_window"), limits.get("secondary_window")) if w]
    except httpx.HTTPError as e:
        raise ProviderError("network") from e
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise ProviderError("refused") from e
    value = {"plan": data.get("plan_type"), "reached": bool(limits.get("limit_reached")), "windows": windows}
    _usage.update(until=time.monotonic() + 60, value=value)
    return value


# --- models and answers --------------------------------------------------------------------------------------------

def list_models() -> list[dict[str, Any]]:
    """What this subscription can use, in the backend's own order. It has no price: the plan's limits apply instead."""
    headers = _headers(_access_token())
    try:
        with _client(20) as c:
            for version in CLIENT_VERSIONS:
                r = c.get(f"{BASE_URL}/models", params={"client_version": version}, headers=headers)
                if r.status_code >= 400:
                    raise ProviderError(_code(r))
                rows = r.json().get("models") or []
                if rows:
                    break
    except httpx.HTTPError as e:
        raise ProviderError("network") from e
    except (ValueError, AttributeError) as e:
        raise ProviderError("refused") from e
    shown = [m for m in rows if isinstance(m, dict) and m.get("slug") and str(m.get("visibility", "")).lower() not in ("hide", "hidden")]
    shown.sort(key=lambda m: (m["priority"] if isinstance(m.get("priority"), (int, float)) else 10_000, m["slug"]))
    if not shown:
        raise ProviderError("refused")
    return [{"id": m["slug"], "name": m.get("display_name") or m["slug"], "price": None} for m in shown]


def to_responses(body: dict[str, Any], model: str) -> dict[str, Any]:
    """Ollama's request in the Responses format this backend speaks: it only streams and keeps nothing (`store`)."""
    system = "\n\n".join(m["content"] for m in body["messages"] if m["role"] == "system")
    turns = [{"role": "assistant" if m["role"] == "assistant" else "user",
              "content": [{"type": "output_text" if m["role"] == "assistant" else "input_text", "text": m["content"]}]}
             for m in body["messages"] if m["role"] != "system"]
    out: dict[str, Any] = {"model": model, "instructions": system or "Answer the student.", "input": turns, "store": False, "stream": True}
    tools = [{"type": "function", "strict": False, **t["function"]} for t in body.get("tools") or []]
    if body.get("web_search"):
        tools.append({"type": "web_search", "external_web_access": True})  # the model decides whether it needs to search
    if tools:
        out["tools"], out["tool_choice"] = tools, "auto"
    if body.get("format"):
        out["text"] = {"format": {"type": "json_schema", "name": "answer", "schema": body["format"], "strict": False}}
    return out


def _read_stream(lines: Iterable[str], deadline: float = float("inf")) -> dict[str, Any]:
    """The final text, tool calls and web sources. Some answers arrive only as deltas, others only in the completed event."""
    text: list[str] = []
    calls: list[dict[str, Any]] = []
    sources: dict[str, str] = {}

    def cited(item: dict[str, Any]) -> None:
        for part in item.get("content") or []:
            for note in part.get("annotations") or []:
                if note.get("type") == "url_citation" and note.get("url"):
                    sources.setdefault(note["url"], note.get("title") or note["url"])

    for line in lines:
        if time.monotonic() > deadline:  # the client's timeout covers each read, and events can keep coming
            raise ProviderError("network")
        if not line.startswith("data:") or line[5:].strip() == "[DONE]":
            continue
        try:
            event = json.loads(line[5:])
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "response.output_text.delta":
            text.append(event.get("delta") or "")
        elif kind == "response.output_item.done" and event.get("item", {}).get("type") == "function_call":
            calls.append(event["item"])
        elif kind == "response.output_item.done" and event.get("item", {}).get("type") == "message":
            cited(event["item"])
        elif kind in ("response.failed", "response.incomplete", "error"):
            detail = json.dumps(event.get("response", {}).get("error") or event.get("error") or event)
            raise ProviderError("quota" if "usage_limit" in detail or "insufficient_quota" in detail else "rate" if "rate_limit" in detail else "refused")
        elif kind == "response.completed":
            output = event.get("response", {}).get("output") or []
            for item in output:
                if item.get("type") == "message":
                    cited(item)
            if not calls:
                calls = [i for i in output if i.get("type") == "function_call"]
            if not text:
                text = [p.get("text", "") for i in output if i.get("type") == "message" for p in i.get("content", []) if p.get("type") == "output_text"]
            return {"content": "".join(text), "tool_calls": [
                {"id": c.get("call_id"), "type": "function", "function": {"name": c.get("name"), "arguments": c.get("arguments") or "{}"}} for c in calls],
                "sources": [{"title": title, "url": url} for url, title in sources.items()]}
    raise ProviderError("network")  # the stream ended before the answer did: nothing partial is ever used


def _ask(request: dict[str, Any], token: str, timeout: float) -> tuple[int, dict[str, Any] | str]:
    with _client(timeout) as c, c.stream("POST", f"{BASE_URL}/responses", headers=_headers(token), json=request) as r:
        if r.status_code >= 400:
            r.read()
            return (r.status_code, _code(r))
        return (r.status_code, _read_stream(r.iter_lines(), time.monotonic() + timeout))


def chat(model: str, body: dict[str, Any], timeout: float) -> httpx.Response:
    """Same promise as providers.chat: one answer in Ollama's shape, every failure a ConnectError."""
    url = f"{BASE_URL}/responses"
    try:
        token = _access_token()
        request = to_responses(body, model)
        status, answer = _ask(request, token, timeout)
        if status == 400 and body.get("format"):  # a model that refuses structured output: ask in words
            ask = {"role": "system", "content": "Responde só com JSON que cumpra este esquema: " + json.dumps(body["format"])}
            status, answer = _ask(to_responses(body | {"format": None, "messages": [ask, *body["messages"]]}, model), token, timeout)
    except ProviderError as e:
        raise httpx.ConnectError(providers._note(ID, e.code).code) from e
    except httpx.HTTPError as e:
        raise httpx.ConnectError(providers._note(ID, "network").code) from e
    if isinstance(answer, str):
        raise httpx.ConnectError(providers._note(ID, answer).code)
    if body.get("format"):
        answer["content"] = providers._unfence(answer["content"])
    return httpx.Response(200, json={"message": answer}, request=httpx.Request("POST", url))

"""Cloud AI providers the student connects with their own API key, behind the same call as the local model.

PEACE's modules speak Ollama's /api/chat. A provider call translates the request to the OpenAI chat format and the
answer back, so no module knows where its answer came from. A new OpenAI-compatible provider is one PROVIDERS entry.
No failure here ever carries the key: errors keep only a code, shown to the student in their language.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from peace import secret_store

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    base_url: str
    keys_url: str
    check_path: str  # answers only with a valid key (OpenRouter's /models is public)
    login: bool = False  # the student logs in on the provider's page instead of pasting a key (peace.chatgpt)


PROVIDERS = {p.id: p for p in (
    Provider("openai", "OpenAI", "https://api.openai.com/v1", "https://platform.openai.com/api-keys", "/models"),
    Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "https://openrouter.ai/keys", "/key"),
    Provider("chatgpt_plan", "Subscrição ChatGPT", "https://chatgpt.com/backend-api/codex", "https://auth.openai.com/codex/device", "", login=True),
)}
# How long to stop calling after each failure, in seconds. A bad key is retried rarely: the worker has no one to tell.
BACKOFF = {"login": 600, "invalid_key": 600, "quota": 300, "model": 300, "rate": 60, "network": 60, "refused": 60}
_down: dict[str, tuple[float, str]] = {}
_offered: dict[str, tuple[float, list[dict[str, Any]]]] = {}
# OpenAI lists every model it has; these cannot hold a conversation.
NOT_CHAT = re.compile(r"embedding|whisper|tts|dall-e|image|audio|realtime|moderation|transcribe|search|babbage|davinci|sora", re.I)


class ProviderError(Exception):
    """`code` is one of BACKOFF's keys."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _secret_name(provider_id: str) -> str:
    return f"llm-key-{provider_id}"


def get_key(provider_id: str) -> str | None:
    return secret_store.get(_secret_name(provider_id))


def has_key(provider_id: str) -> bool:
    return bool(get_key(provider_id))


def save_key(provider_id: str, key: str) -> str:
    _down.pop(provider_id, None)
    _offered.pop(provider_id, None)
    return secret_store.put(_secret_name(provider_id), key)


def remove_key(provider_id: str) -> None:
    _down.pop(provider_id, None)
    _offered.pop(provider_id, None)
    secret_store.delete(_secret_name(provider_id))


def error(provider_id: str) -> str | None:
    """Why this provider is not being called right now."""
    until, code = _down.get(provider_id, (0.0, ""))
    return code if time.monotonic() < until else None


def usable(provider_id: str) -> bool:
    return not error(provider_id) and has_key(provider_id)


def can_search(provider_id: str) -> bool:
    """Can this provider look things up on the web by itself? Only the subscription's backend, for now."""
    return PROVIDERS[provider_id].login


def _note(provider_id: str, code: str) -> ProviderError:
    _down[provider_id] = (time.monotonic() + BACKOFF[code], code)
    log.warning("%s: %s", provider_id, code)
    return ProviderError(code)


def _code(r: httpx.Response) -> str:
    if r.status_code in (401, 403):
        return "invalid_key"
    if r.status_code == 402:
        return "quota"
    if r.status_code == 429:
        return "quota" if "insufficient_quota" in r.text else "rate"
    if r.status_code == 404:
        return "model"
    return "network" if r.status_code >= 500 else "refused"


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def check_key(provider_id: str, key: str) -> None:
    """Raises ProviderError unless the provider accepts this key. Sends it only to that provider, over https."""
    p = PROVIDERS[provider_id]
    try:
        r = httpx.get(p.base_url + p.check_path, headers=_headers(key), timeout=15)
    except httpx.HTTPError as e:
        raise ProviderError("network") from e
    if r.status_code >= 400:
        raise ProviderError(_code(r))


def list_models(provider_id: str) -> list[dict[str, Any]]:
    """The models this key can talk to that can also pick PEACE's commands. `price`: dollars per million input
    tokens when the provider says (OpenAI's list does not)."""
    if PROVIDERS[provider_id].login:
        from peace import chatgpt  # it builds on this module
        return chatgpt.list_models()
    key = get_key(provider_id)
    if not key:
        raise ProviderError("invalid_key")
    try:
        r = httpx.get(PROVIDERS[provider_id].base_url + "/models", headers=_headers(key), timeout=20)
    except httpx.HTTPError as e:
        raise ProviderError("network") from e
    if r.status_code >= 400:
        raise ProviderError(_code(r))
    try:
        rows = r.json()["data"]
        models = [_model(m) for m in rows if _can_chat(m)]
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise ProviderError("refused") from e
    return sorted(models, key=lambda m: m["id"])


def offered(provider_id: str) -> list[dict[str, Any]]:
    """list_models for what is asked often (the main page), kept 10 minutes. A failure keeps the last list and is retried in a minute."""
    until, rows = _offered.get(provider_id, (0.0, []))
    if time.monotonic() < until:
        return rows
    try:
        rows, wait = list_models(provider_id), 600
    except ProviderError:
        wait = 60
    _offered[provider_id] = (time.monotonic() + wait, rows)
    return rows


def _can_chat(m: dict[str, Any]) -> bool:
    if "supported_parameters" in m:  # OpenRouter says what each model supports
        return "tools" in m["supported_parameters"]
    return not NOT_CHAT.search(m["id"])


def _model(m: dict[str, Any]) -> dict[str, Any]:
    try:
        price: float | None = round(float(m["pricing"]["prompt"]) * 1e6, 3)
    except (KeyError, TypeError, ValueError):
        price = None
    return {"id": m["id"], "name": m.get("name") or m["id"], "price": price}


def to_openai(body: dict[str, Any], model: str) -> dict[str, Any]:
    """Only what the student's request needs: Ollama's options (context size, keep-alive) mean nothing there, and some
    models refuse a temperature."""
    out: dict[str, Any] = {"model": model, "stream": False, "messages": [{"role": m["role"], "content": m["content"]} for m in body["messages"]]}
    if body.get("tools"):
        out["tools"] = body["tools"]
    if body.get("format"):
        out["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": body["format"]}}
    return out


def _without_schema(out: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """For a model that refuses structured output: ask in words. PEACE validates the answer anyway."""
    ask = {"role": "system", "content": "Responde só com JSON que cumpra este esquema: " + json.dumps(schema)}
    return {k: v for k, v in out.items() if k != "response_format"} | {"messages": [ask, *out["messages"]]}


def _unfence(text: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())


def chat(provider_id: str, model: str, body: dict[str, Any], timeout: float) -> httpx.Response:
    """One model answer, in Ollama's shape. Every failure is a ConnectError, which callers already read as "the model
    is away, try later", so a spent key never marks a document as read."""
    p = PROVIDERS[provider_id]
    if p.login:
        from peace import chatgpt  # it builds on this module
        return chatgpt.chat(model, body, timeout)
    key = get_key(provider_id)
    if not key:
        raise httpx.ConnectError(f"{provider_id}: no key")
    request = to_openai(body, model)
    url = p.base_url + "/chat/completions"
    try:
        r = httpx.post(url, headers=_headers(key), json=request, timeout=timeout)
        if r.status_code == 400 and body.get("format"):
            r = httpx.post(url, headers=_headers(key), json=_without_schema(request, body["format"]), timeout=timeout)
    except httpx.HTTPError as e:
        raise httpx.ConnectError(_note(provider_id, "network").code) from e
    if r.status_code >= 400:
        raise httpx.ConnectError(_note(provider_id, _code(r)).code)
    try:
        message = r.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise httpx.ConnectError(_note(provider_id, "refused").code) from e
    content = message.get("content") or ""
    answer = {"content": _unfence(content) if body.get("format") else content, "tool_calls": message.get("tool_calls") or []}
    return httpx.Response(200, json={"message": answer}, request=httpx.Request("POST", url))

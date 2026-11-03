"""Client for the language model, shared by the assistant and the worker: Ollama on this computer, or a cloud provider
the student connected. What may reach the provider is decided here, per purpose, never by the calling module:
"local" stays on this computer whatever is chosen, the others follow the student's switches (SHARE_DEFAULTS).
"""

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from peace import providers

log = logging.getLogger(__name__)

# Ollama on this computer (PEACE_LLM_URL), or a forwarded Unix socket (PEACE_LLM_SOCKET) when PEACE runs elsewhere;
# through a socket the Host header must still read 127.0.0.1:11434 or Ollama answers 403.
LLM_URL = os.environ.get("PEACE_LLM_URL", "http://127.0.0.1:11434").rstrip("/")
LLM_SOCKET = os.environ.get("PEACE_LLM_SOCKET")
# Models that can pick PEACE's commands (tool calling), best first. The Windows installer offers these, recommending by this PC's memory.
PREFERRED = ("gpt-oss:20b", "qwen3:8b", "qwen3:4b", "llama3.2:3b")
LLM_TIMEOUT = float(os.environ.get("PEACE_LLM_TIMEOUT", "45"))  # only for answering: loading has its own, longer limit
LOAD_TIMEOUT = 900.0  # 8 s with free RAM, 87 s with swap full, minutes when the file left the disk cache
# One context size for every caller: Ollama reloads the model (~11 s) whenever it changes.
# use_mmap off: with RAM tight the mapped load failed after 5 min; read directly it takes 6 s (2/11, btrfs, ROCm).
LLM_OPTIONS = {"temperature": 0, "num_ctx": 8192, "use_mmap": False}
KEEP_ALIVE = os.environ.get("PEACE_LLM_KEEP_ALIVE", "30m")  # Ollama unloads the model after this long unused
# Loading streams the whole model file through RAM into the GPU. With a game holding the RAM (27/10: Minecraft, swap
# full) a 13 GB load did not finish in 2 min. So PEACE only loads when both have room; otherwise it uses its rules.
VRAM_MARGIN = int(os.environ.get("PEACE_LLM_VRAM_MARGIN_MB", "512")) * 2**20
MIN_FREE_RAM = int(os.environ.get("PEACE_LLM_MIN_FREE_RAM_MB", "0")) * 2**20  # 0: decided from where the model goes
DRM = Path(os.environ.get("PEACE_DRM_DIR", "/sys/class/drm"))
MEMINFO = Path("/proc/meminfo")


def can_search(purpose: str = "chat") -> bool:
    return bool((cloud := route(purpose)) and providers.can_search(cloud[0]))


def _request(method: str, path: str, **kw: Any) -> httpx.Response:
    transport = httpx.HTTPTransport(uds=LLM_SOCKET) if LLM_SOCKET else None
    with httpx.Client(transport=transport, base_url=LLM_URL) as client:
        return client.request(method, path, **kw)


# background: reading every file in the library unasked, which can cost real credit on a provider.
SHARE_DEFAULTS = {"chat": True, "documents": True, "background": False, "mail": False}


def ai_settings() -> dict[str, Any]:
    """The "assistant" preference: provider ("" = local), cloud_model, favourites, previous, share, model (local)."""
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session

    from peace.db import get_engine
    from peace.models import Preference

    try:
        with Session(get_engine()) as session:
            row = session.get(Preference, "assistant")
    except (SQLAlchemyError, KeyError, OSError):  # no database configured or reachable: the environment decides
        row = None
    return dict(row.value) if row else {}


def route(purpose: str = "local") -> tuple[str, str] | None:
    """(provider, model) when this purpose goes to the cloud; None when it stays on this computer."""
    if purpose == "local":
        return None
    prefs = ai_settings()
    provider, model = prefs.get("provider"), prefs.get("cloud_model")
    if provider not in providers.PROVIDERS or not model:
        return None
    return (provider, model) if (SHARE_DEFAULTS | prefs.get("share", {})).get(purpose) else None


def _llm(method: str, path: str, purpose: str = "local", **kw: Any) -> httpx.Response:
    if path == "/api/chat":
        body = kw.get("json", {})
        if body.get("model") != VISION_MODEL:
            if cloud := route(purpose):
                return providers.chat(*cloud, body, kw.get("timeout", LLM_TIMEOUT))
            kw["json"] = body = body | {"model": local_model()}  # callers name the active model, which may be a cloud one
        model = body.get("model")
        # A cold load outlasts any caller's timeout, and a caller giving up cancels the load, so it would never finish.
        # The load runs on its own and the caller gets "not available yet" (it answers without the model meanwhile).
        if model != VISION_MODEL and not any(m["name"] == model for m in loaded_models()):
            load_in_background(model)
            raise httpx.ConnectError(f"{model} is loading")
        _last_call["at"] = time.time()
    return _request(method, path, **kw)


_loading: dict[str, Any] = {"model": None}
_loading_lock = threading.Lock()


def load_in_background(model: str) -> None:
    """Load the model once, with nobody waiting on it."""
    with _loading_lock:
        if _loading["model"]:
            return
        _loading["model"] = model

    def run() -> None:
        try:
            _last_call["at"] = time.time()
            _request("POST", "/api/generate", timeout=LOAD_TIMEOUT, json={"model": model, "keep_alive": KEEP_ALIVE, "options": LLM_OPTIONS}).raise_for_status()
            _last_call["at"] = time.time()
        except httpx.HTTPError as e:
            log.warning("could not load %s: %s", model, e)
        finally:
            _loading["model"] = None
            forget_model_check()

    threading.Thread(target=run, daemon=True, name="llm-load").start()


LOADING = "a carregar o modelo"
_reachable: dict[str, Any] = {"at": -60.0, "ok": False, "model": None, "installed": [], "sizes": {}, "paused": None}
_vram_seen: dict[str, int] = {}  # what each model really took on the GPU when last seen loaded
_last_call = {"at": 0.0}  # wall-clock time of PEACE's last request to the model


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


def local_model() -> str:
    """The local model in use (as of the last check); the first preferred one when nothing is known yet."""
    return _reachable["model"] or wanted_model() or PREFERRED[0]


def current_model() -> str:
    """The model the conversation is using."""
    cloud = route("chat")
    return cloud[1] if cloud else local_model()


def installed_models() -> list[str]:
    llm_reachable()
    return _reachable["installed"]


def llm_reachable(purpose: str = "local") -> bool:
    """Is a usable model being served right now, without fighting other programs for memory? Cached for 30 s so
    status polling stays cheap; resources are only read here, never polled."""
    if cloud := route(purpose):
        return providers.usable(cloud[0])
    if _loading["model"]:  # its memory is filling up: that is not another program competing
        _reachable.update(ok=False, model=_loading["model"], paused=LOADING)
        return False
    if time.monotonic() - _reachable["at"] < 30:
        return _reachable["ok"]
    try:
        tags = _llm("GET", "/api/tags", timeout=1.5).json()
        models = [m for m in tags.get("models", []) if isinstance(m.get("name"), str)]
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        models = []
    installed = [m["name"] for m in models]
    sizes = {m["name"]: int(m.get("size") or 0) for m in models}
    chosen = pick_model(installed, wanted_model())
    paused = room_to_load(chosen, sizes.get(chosen, 0)) if chosen else None
    _reachable.update(at=time.monotonic(), ok=chosen is not None and not paused, model=chosen, installed=installed,
                      sizes=sizes, paused=paused)
    return chosen is not None and not paused


def _meminfo(key: str) -> int | None:
    try:
        line = next(x for x in MEMINFO.read_text().splitlines() if x.startswith(key + ":"))
        return int(line.split()[1]) * 1024
    except (OSError, StopIteration, ValueError, IndexError):
        return None


def gpu_memory() -> dict[str, int] | None:
    """VRAM of the GPU with the most of it (AMD sysfs counters), in bytes; None where the kernel does not say."""
    best = None
    for card in DRM.glob("card[0-9]*/device"):
        try:
            total, used = (int((card / f"mem_info_vram_{k}").read_text()) for k in ("total", "used"))
        except (OSError, ValueError):
            continue
        if not best or total > best["total"]:
            best = {"total": total, "used": used, "free": total - used}
    return best


def loaded_models() -> list[dict[str, Any]]:
    """What Ollama holds in memory now (name, bytes in VRAM, when it expires)."""
    try:
        models = [{"name": m["name"], "size_vram": int(m.get("size_vram") or 0), "expires_at": m.get("expires_at")}
                  for m in _llm("GET", "/api/ps", timeout=1.5).json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return []
    _vram_seen.update({m["name"]: m["size_vram"] for m in models if m["size_vram"]})
    return models


def room_to_load(model: str, size: int, swap: bool = False) -> str | None:
    """Why loading the model now would compete with other programs, or None when it is loaded or fits. With `swap`,
    what Ollama already holds counts as free: Ollama unloads its own models to make room, never a game's memory."""
    loaded = loaded_models()
    if any(m["name"] == model for m in loaded):
        return None  # already in memory: using it costs nothing more
    gpu, ram = gpu_memory(), _meminfo("MemAvailable")
    if gpu and swap:
        gpu = {**gpu, "free": gpu["free"] + sum(m["size_vram"] for m in loaded)}
    need = (_vram_seen.get(model) or int(size * 0.9)) + VRAM_MARGIN  # ~10 % of the file stays in RAM (gpt-oss:20b: 12.2 of 13.8 GB)
    if gpu and size and gpu["free"] < need:
        return f"memória da placa gráfica ocupada ({gpu['free'] // 2**20} MiB livres, o modelo precisa de {need // 2**20})"
    # With a GPU it fits on, the weights leave RAM once loaded, but reading them under pressure stalls: 7.6 GB free never
    # finished (27/10, swap full), 11 GB took 90 s (2/11). Without a GPU, all of it stays.
    need_ram = MIN_FREE_RAM or (int(size * 0.7) if gpu else size)
    if ram is not None and size and ram < need_ram:
        return f"memória do PC ocupada ({ram // 2**20} MiB disponíveis, carregar o modelo precisa de {need_ram // 2**20})"
    return None


# Reads exam photos only (phase 7); everything else stays on the text model. Loaded per batch, swapped with it.
VISION_MODEL = os.environ.get("PEACE_VISION_MODEL", "qwen2.5vl:7b")
VISION_OPTIONS = {"temperature": 0, "num_ctx": 8192, "num_predict": 3000, "repeat_penalty": 1.1}  # it loops on diagrams otherwise


def vision_unavailable() -> str | None:
    """Why the image reader cannot run now (in Portuguese, for the student), or None when it can."""
    llm_reachable()
    if VISION_MODEL not in _reachable["installed"]:
        return f"o leitor de imagens ({VISION_MODEL}) não está instalado"
    return room_to_load(VISION_MODEL, _reachable["sizes"].get(VISION_MODEL, 0), swap=True)


def see_json(prompt: str, image: bytes, schema: dict[str, Any], timeout: float = 300) -> dict[str, Any] | None:
    """What the image model reads in one picture, forced into `schema` (the picture is untrusted data).
    None when the model failed; {} when it answered outside the schema."""
    import base64

    try:
        r = _llm("POST", "/api/chat", timeout=timeout, json={
            "model": VISION_MODEL, "stream": False, "format": schema, "keep_alive": "1m", "options": VISION_OPTIONS,
            "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(image).decode()]}],
        })
        r.raise_for_status()
        data = json.loads(r.json()["message"]["content"])
    except httpx.HTTPStatusError as e:
        if e.response.status_code >= 500 or e.response.status_code == 404:
            return None
        log.warning("image model request refused: %s", e)
        return {}
    except httpx.TransportError:
        return None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("image model answered outside the schema: %s", e)
        return {}
    return data if isinstance(data, dict) else {}


def release_if_contended() -> str | None:
    """Unload PEACE's model when it sits idle while another program needs the memory. Only PEACE's own model, only
    when nobody used it after PEACE did (Ollama's expiry is still the one PEACE's last call set), never anything else."""
    model, last = _reachable["model"], _last_call["at"]
    if not last or time.time() - last > _keep_alive_seconds() + 60:
        return None  # PEACE has not used it lately: nothing of PEACE's can be loaded, nothing to check
    mine = next((m for m in loaded_models() if m["name"] == model), None)
    if not mine or not last or time.time() - last < 60:
        return None
    gpu, ram = gpu_memory(), _meminfo("MemAvailable")
    if not ((gpu and gpu["free"] < VRAM_MARGIN) or (ram is not None and ram < 2 * 2**30)):
        return None
    try:
        expires = datetime.fromisoformat(mine["expires_at"]).timestamp()
    except (TypeError, ValueError):
        return None
    if expires > last + _keep_alive_seconds() + 30:  # someone used it after PEACE: not PEACE's to unload
        return None
    try:
        _llm("POST", "/api/generate", timeout=10, json={"model": model, "keep_alive": 0}).raise_for_status()
    except httpx.HTTPError:
        return None
    forget_model_check()
    return model


def _keep_alive_seconds() -> float:
    unit = {"s": 1, "m": 60, "h": 3600}.get(KEEP_ALIVE[-1:], 1)
    try:
        return float(KEEP_ALIVE.rstrip("smh")) * unit
    except ValueError:
        return 600.0


def pause_reason() -> str | None:
    """Why the model is not used right now although it is installed (from the last check), for the status."""
    if cloud := route("chat"):
        return providers.error(cloud[0])
    return _reachable["paused"]


def forget_model_check() -> None:
    """The choice changed in Settings: check again on the next call."""
    _reachable["at"] = -60.0


def mark_unreachable() -> None:
    """A call just failed: report offline until the next check."""
    _reachable.update(at=time.monotonic(), ok=False)


def ask_json(system: str, user: str, schema: dict[str, Any], timeout: float = 180, purpose: str = "local") -> dict[str, Any] | None:
    """One answer forced into `schema` (the model has no tools; `user` carries untrusted text as data).
    None when the model failed (not the input's fault: try again later); {} when it answered outside the schema."""
    import json

    try:
        r = _llm("POST", "/api/chat", purpose=purpose, timeout=timeout, json={
            "model": current_model(), "stream": False, "think": "low", "format": schema, "keep_alive": KEEP_ALIVE,
            "options": LLM_OPTIONS, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        r.raise_for_status()
        data = json.loads(r.json()["message"]["content"])
    except httpx.HTTPStatusError as e:
        if e.response.status_code >= 500 or e.response.status_code == 404:  # out of memory, loading, model missing
            mark_unreachable()
            return None
        log.warning("model request refused: %s", e)
        return {}
    except httpx.TransportError:
        mark_unreachable()
        return None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("model answered outside the schema: %s", e)
        return {}
    return data if isinstance(data, dict) else {}

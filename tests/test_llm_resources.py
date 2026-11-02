import httpx
import threading
"""The GPU/RAM policy for the local model: load only when there is room, give it back when a game needs it."""
import time
from datetime import UTC, datetime, timedelta

import pytest

from peace import llm

GiB = 2**30


class FakeOllama:
    def __init__(self, loaded=(), size=13 * GiB):
        self.loaded = list(loaded)
        self.size = size
        self.posts = []

    def __call__(self, method, path, **kw):
        if path == "/api/chat":
            llm._last_call["at"] = time.time()
        if path == "/api/tags":
            body = {"models": [{"name": "gpt-oss:20b", "size": self.size}]}
        elif path == "/api/ps":
            body = {"models": self.loaded}
        else:
            self.posts.append((path, kw.get("json")))
            self.loaded = []
            body = {}
        return type("R", (), {"json": lambda s: body, "raise_for_status": lambda s: None})()


@pytest.fixture
def machine(monkeypatch, tmp_path):
    card = tmp_path / "drm" / "card1" / "device"
    card.mkdir(parents=True)
    meminfo = tmp_path / "meminfo"
    monkeypatch.setattr(llm, "DRM", tmp_path / "drm")
    monkeypatch.setattr(llm, "MEMINFO", meminfo)
    monkeypatch.setattr(llm, "wanted_model", lambda: "")
    monkeypatch.setattr(llm, "_vram_seen", {})
    monkeypatch.setattr(llm, "_last_call", {"at": 0.0})
    llm.forget_model_check()

    def set_(vram_used_gib, ram_avail_gib):
        (card / "mem_info_vram_total").write_text(str(16 * GiB))
        (card / "mem_info_vram_used").write_text(str(int(vram_used_gib * GiB)))
        meminfo.write_text(f"MemTotal: {30 * 2**20} kB\nMemAvailable: {int(ram_avail_gib * 2**20)} kB\n")
        llm.forget_model_check()
    yield set_
    llm.forget_model_check()


def test_the_model_loads_only_when_gpu_and_ram_have_room(machine, monkeypatch):
    monkeypatch.setattr(llm, "_llm", FakeOllama())
    machine(vram_used_gib=2, ram_avail_gib=20)
    assert llm.llm_reachable() and llm.pause_reason() is None
    machine(vram_used_gib=5, ram_avail_gib=20)  # a game holds the GPU
    assert not llm.llm_reachable() and "placa gráfica" in llm.pause_reason()
    machine(vram_used_gib=2, ram_avail_gib=7.6)  # 27/10: Minecraft, swap full; a 13 GB load never finished
    assert not llm.llm_reachable() and "memória do PC" in llm.pause_reason()


def test_real_machine_states_that_wrongly_said_offline(machine, monkeypatch):
    monkeypatch.setattr(llm, "_llm", FakeOllama())
    machine(vram_used_gib=2.55, ram_avail_gib=20)  # 2/11: 13155 MiB free, refused for 22 MiB with a 1 GB margin
    assert llm.llm_reachable()
    machine(vram_used_gib=2, ram_avail_gib=11.2)  # 2/11: swap full but 11 GB available; it loaded in 90 s
    assert llm.llm_reachable()


def test_a_loaded_model_is_used_whatever_the_memory(machine, monkeypatch):
    monkeypatch.setattr(llm, "_llm", FakeOllama(loaded=[{"name": "gpt-oss:20b", "size_vram": 12 * GiB, "expires_at": None}]))
    machine(vram_used_gib=15.5, ram_avail_gib=1)
    assert llm.llm_reachable()


def test_the_real_vram_use_replaces_the_estimate_once_seen(machine, monkeypatch):
    fake = FakeOllama(loaded=[{"name": "gpt-oss:20b", "size_vram": 10 * GiB, "expires_at": None}])
    monkeypatch.setattr(llm, "_llm", fake)
    machine(vram_used_gib=4.5, ram_avail_gib=20)
    llm.loaded_models()
    fake.loaded = []
    assert llm.room_to_load("gpt-oss:20b", 13 * GiB) is None  # 11.5 free >= 10 + margin (the estimate would say 11.7 + margin)


def _loaded_until(minutes_from_now):
    return [{"name": "gpt-oss:20b", "size_vram": 12 * GiB,
             "expires_at": (datetime.now(UTC) + timedelta(minutes=minutes_from_now)).isoformat()}]


def test_an_idle_model_is_given_back_when_a_game_needs_the_memory(machine, monkeypatch):
    fake = FakeOllama(loaded=_loaded_until(8))
    monkeypatch.setattr(llm, "_llm", fake)
    machine(vram_used_gib=15.6, ram_avail_gib=20)
    llm.llm_reachable()
    llm._last_call["at"] = time.time() - 120  # PEACE used it 2 min ago; it expires in 8 (keep_alive 10m)
    assert llm.release_if_contended() == "gpt-oss:20b"
    assert fake.posts == [("/api/generate", {"model": "gpt-oss:20b", "keep_alive": 0})]


@pytest.mark.parametrize(("used_ago", "expires_in", "vram_used", "why"), [
    (20, 9.6, 15.6, "PEACE used it seconds ago"),
    (120, 45, 15.6, "someone else used it after PEACE (their keep_alive)"),
    (120, 8, 6, "no pressure"),
    (None, 8, 15.6, "PEACE never used it"),
])
def test_the_model_is_left_alone_otherwise(machine, monkeypatch, used_ago, expires_in, vram_used, why):
    fake = FakeOllama(loaded=_loaded_until(expires_in))
    monkeypatch.setattr(llm, "_llm", fake)
    machine(vram_used_gib=vram_used, ram_avail_gib=20)
    llm.llm_reachable()
    llm._last_call["at"] = time.time() - used_ago if used_ago else 0.0
    assert llm.release_if_contended() is None and fake.posts == [], why


def test_idle_peace_does_not_even_ask_ollama(machine, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "_llm", lambda *a, **k: calls.append(a))
    llm._last_call["at"] = time.time() - 3600
    assert llm.release_if_contended() is None and calls == []


def test_a_cold_model_loads_on_its_own_and_callers_are_told_to_come_back(monkeypatch):
    llm.forget_model_check()
    started, release = [], threading.Event()

    def request(method, path, **kw):
        started.append((path, kw["json"]["model"], kw["timeout"]))
        release.wait(5)
        return httpx.Response(200, request=httpx.Request(method, "http://x"))

    monkeypatch.setattr(llm, "_request", request)
    monkeypatch.setattr(llm, "loaded_models", lambda: [])
    chat = {"model": "gpt-oss:20b", "messages": []}
    with pytest.raises(httpx.TransportError):
        llm._llm("POST", "/api/chat", timeout=45, json=chat)
    with pytest.raises(httpx.TransportError):  # asked again while loading: no second load
        llm._llm("POST", "/api/chat", timeout=45, json=chat)
    deadline = time.monotonic() + 2
    while not started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started == [("/api/generate", "gpt-oss:20b", llm.LOAD_TIMEOUT)]  # nobody's 45 s can cancel it
    assert not llm.llm_reachable() and llm.pause_reason() == llm.LOADING
    release.set()
    while llm._loading["model"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert llm._loading["model"] is None
    monkeypatch.setattr(llm, "loaded_models", lambda: [{"name": "gpt-oss:20b", "size_vram": 1, "expires_at": None}])
    started.clear()
    llm._llm("POST", "/api/chat", timeout=45, json=chat)
    assert started == [("/api/chat", "gpt-oss:20b", 45)]

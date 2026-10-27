"""Brave native messaging: the host's framing, validation and relay (desktop/brave/peace_browser_host.py), the
extension's own checks (Node), and that the host manifest only admits the PEACE extension's fixed ID."""

import base64
import hashlib
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

BRAVE = Path(__file__).parents[1] / "desktop" / "brave"
spec = importlib.util.spec_from_file_location("peace_browser_host", BRAVE / "peace_browser_host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def frame(message) -> bytes:
    return host.encode(message)


def test_messages_are_length_prefixed_json_with_the_browser_limit():
    stream = io.BytesIO(frame({"id": "a", "ok": True}) + frame({"id": "b", "ok": False}))
    assert [host.read_message(stream), host.read_message(stream), host.read_message(stream)] == \
        [{"id": "a", "ok": True}, {"id": "b", "ok": False}, None]
    assert host.read_message(io.BytesIO(frame({"x": 1})[:-2])) is None
    with pytest.raises(ValueError):
        host.read_message(io.BytesIO((host.MAX_MESSAGE + 1).to_bytes(4, "little") + b"x"))
    with pytest.raises(ValueError):
        host.encode({"x": "y" * host.MAX_MESSAGE})


@pytest.mark.parametrize("request_", [
    None, [], {"id": "a", "op": "list"}, {"id": "a", "op": "list", "args": {}, "extra": 1}, {"id": "a b", "op": "list", "args": {}},
    {"id": "a", "op": "eval", "args": {}}, {"id": "a", "op": "open", "args": {"url": "javascript:alert(1)"}},
    {"id": "a", "op": "open", "args": {"url": "https://x\ny"}}, {"id": "a", "op": "close", "args": {"tabIds": [True]}},
    {"id": "a", "op": "close", "args": {"tabIds": list(range(51))}}, {"id": "a", "op": "move", "args": {"tabIds": [1]}},
    {"id": "a", "op": "move", "args": {"tabIds": [1], "index": True}}, {"id": "a", "op": "group", "args": {"tabIds": [1], "title": ""}},
    {"id": "a", "op": "group", "args": {"tabIds": [1], "title": "x", "color": "black"}}, {"id": "a", "op": "focus", "args": {"tabId": -1}},
])
def test_requests_from_the_agent_are_checked_before_reaching_the_browser(request_):
    out = io.BytesIO()
    reply = host.Bridge(out).ask(request_)
    assert reply["ok"] is False and out.getvalue() == b""


def test_answers_are_matched_by_id_and_a_silent_browser_times_out(monkeypatch):
    out = io.BytesIO()
    bridge = host.Bridge(out)
    monkeypatch.setattr(host, "TIMEOUT", 0.2)

    def browser():
        while not out.getvalue():
            pass
        sent = host.read_message(io.BytesIO(out.getvalue()))
        bridge.answer({"id": "other", "ok": True, "result": "wrong"})
        bridge.answer("garbage")
        bridge.answer({"id": sent["id"], "ok": True, "result": [{"id": 1}], "error": None})

    threading.Thread(target=browser, daemon=True).start()
    assert bridge.ask({"id": "r1", "op": "list", "args": {}}) == {"id": "r1", "ok": True, "result": [{"id": 1}], "error": None}
    assert bridge.ask({"id": "r2", "op": "list", "args": {}}) == {"id": "r2", "ok": False, "error": "o Brave não respondeu"}


def test_the_socket_is_private_and_relays_one_request_per_connection(monkeypatch):
    root = Path(tempfile.mkdtemp(prefix="pb"))
    try:
        r, w = os.pipe()
        out = os.fdopen(w, "wb")
        bridge = host.Bridge(out)
        server = host.serve(bridge, root / "peace-browser.sock")
        threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
        assert (root / "peace-browser.sock").stat().st_mode & 0o777 == 0o600

        def extension():  # reads what the host forwards and answers like background.js
            with os.fdopen(r, "rb") as stream:
                msg = host.read_message(stream)
                bridge.answer({"id": msg["id"], "ok": True, "result": msg["op"]})

        threading.Thread(target=extension, daemon=True).start()
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(root / "peace-browser.sock"))
            s.sendall(json.dumps({"id": "q1", "op": "list", "args": {}}).encode() + b"\n")
            assert json.loads(s.makefile().readline())["result"] == "list"
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(root / "peace-browser.sock"))
            s.sendall(b"not json\n")
            assert json.loads(s.makefile().readline()) == {"id": None, "ok": False, "error": "mensagem inválida"}
        server.shutdown()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_host_manifest_admits_only_the_peace_extension():
    manifest = json.loads((BRAVE / "extension" / "manifest.json").read_text())
    der = base64.b64decode(manifest["key"])
    extension_id = "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(der).hexdigest()[:32])
    native = json.loads((BRAVE / "com.peace.browser.json").read_text())
    assert native["allowed_origins"] == [f"chrome-extension://{extension_id}/"] and native["type"] == "stdio"
    assert native["name"] == "com.peace.browser" and native["path"] == "@HOST_PATH@"
    # Minimum permissions: no host access, scripting, cookies, history, debugger or web requests.
    assert sorted(manifest["permissions"]) == ["nativeMessaging", "tabGroups", "tabs"]
    assert not {"host_permissions", "content_scripts", "optional_permissions", "externally_connectable"} & set(manifest)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_extension_message_handling():
    proc = subprocess.run(["node", "--test", str(Path(__file__).with_name("browser_extension.test.mjs"))],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]

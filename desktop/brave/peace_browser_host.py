#!/usr/bin/env python3
"""Native-messaging host for the PEACE Brave extension (com.peace.browser).

Brave starts this when the extension connects and talks to it over stdin/stdout (4-byte length + JSON). It
listens on $XDG_RUNTIME_DIR/peace-browser.sock (mode 600, this user only) where the PEACE desktop agent sends
one request per connection; the request is checked, relayed to the extension, and the matching answer goes
back. Nothing is exposed on the network and no remote-debugging port is used. Standard library only.
"""

import json
import os
import re
import socket
import socketserver
import struct
import sys
import threading
from pathlib import Path

MAX_MESSAGE = 1 << 20  # Chromium's limit for host -> browser messages
TIMEOUT = 5
COLORS = {"grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"}
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
URL = re.compile(r"^https?://[^\s\x00-\x1f]{1,1990}$")


def _int(v, low: int, high: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and low <= v <= high


def _tab_ids(v) -> bool:
    return isinstance(v, list) and 1 <= len(v) <= 50 and all(_int(x, 0, 2**31 - 1) for x in v)


OPS = {
    "list": lambda a: a == {},
    "open": lambda a: set(a) == {"url"} and isinstance(a["url"], str) and bool(URL.match(a["url"])),
    "focus": lambda a: set(a) == {"tabId"} and _int(a["tabId"], 0, 2**31 - 1),
    "move": lambda a: set(a) <= {"tabIds", "index", "windowId"} and _tab_ids(a.get("tabIds")) and _int(a.get("index"), -1, 500)
    and ("windowId" not in a or _int(a["windowId"], 0, 2**31 - 1)),
    "group": lambda a: set(a) <= {"tabIds", "title", "color"} and _tab_ids(a.get("tabIds")) and isinstance(a.get("title"), str)
    and 1 <= len(a["title"]) <= 40 and a.get("color", "grey") in COLORS,
    "ungroup": lambda a: set(a) == {"tabIds"} and _tab_ids(a["tabIds"]),
    "close": lambda a: set(a) == {"tabIds"} and _tab_ids(a["tabIds"]),
}


def check(request) -> str | None:
    """Why a request from the agent is refused, or None."""
    if not isinstance(request, dict) or set(request) != {"id", "op", "args"}:
        return "mensagem inválida"
    if not isinstance(request["id"], str) or not ID.match(request["id"]):
        return "id inválido"
    if request["op"] not in OPS:
        return "operação desconhecida"
    if not isinstance(request["args"], dict) or not OPS[request["op"]](request["args"]):
        return "argumentos inválidos"
    return None


def encode(message: dict) -> bytes:
    data = json.dumps(message).encode()
    if len(data) > MAX_MESSAGE:
        raise ValueError("message too large")
    return struct.pack("=I", len(data)) + data


def read_message(stream) -> dict | None:
    """One message from the browser, or None at end of input; oversized or malformed input ends the host."""
    header = stream.read(4)
    if len(header) < 4:
        return None
    (size,) = struct.unpack("=I", header)
    if size > MAX_MESSAGE:
        raise ValueError("message too large")
    data = stream.read(size)
    if len(data) < size:
        return None
    return json.loads(data)


class Bridge:
    def __init__(self, out):
        self.out = out
        self.lock = threading.Lock()
        self.waiting: dict[str, list] = {}

    def ask(self, request) -> dict:
        problem = check(request)
        if problem:
            rid = request.get("id") if isinstance(request, dict) and isinstance(request.get("id"), str) else None
            return {"id": rid and rid[:64], "ok": False, "error": problem}
        slot = [threading.Event(), None]
        with self.lock:
            if request["id"] in self.waiting:
                return {"id": request["id"], "ok": False, "error": "id repetido"}
            self.waiting[request["id"]] = slot
            self.out.write(encode(request))
            self.out.flush()
        try:
            if not slot[0].wait(TIMEOUT):
                return {"id": request["id"], "ok": False, "error": "o Brave não respondeu"}
            return slot[1]
        finally:
            with self.lock:
                self.waiting.pop(request["id"], None)

    def answer(self, reply) -> None:
        if not isinstance(reply, dict) or not isinstance(reply.get("id"), str) or not isinstance(reply.get("ok"), bool):
            return  # nothing to match it with
        with self.lock:
            slot = self.waiting.get(reply["id"])
        if slot:
            slot[1] = {"id": reply["id"], "ok": reply["ok"], "result": reply.get("result"),
                       "error": str(reply.get("error") or "")[:200] or None}
            slot[0].set()


def same_user(conn: socket.socket) -> bool:
    _, uid, _ = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return uid == os.getuid()


def serve(bridge: Bridge, path: Path) -> socketserver.UnixStreamServer:
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.request.settimeout(TIMEOUT + 2)
            if not same_user(self.request):
                return
            try:
                request = json.loads(self.rfile.readline(64 * 1024))
            except ValueError:
                request = None
            self.wfile.write(json.dumps(bridge.ask(request)).encode() + b"\n")

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    old = os.umask(0o177)  # the socket is born private, no window before the chmod
    try:
        path.unlink(missing_ok=True)
        server = Server(str(path), Handler)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    return server


def main() -> None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        sys.exit("XDG_RUNTIME_DIR is not set")
    path = Path(runtime) / "peace-browser.sock"
    bridge = Bridge(sys.stdout.buffer)
    server = serve(bridge, path)
    inode = path.stat().st_ino
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        while (message := read_message(sys.stdin.buffer)) is not None:
            bridge.answer(message)
    except (ValueError, OSError):
        pass
    finally:  # Brave closed the port: stop answering for it, but never remove a newer host's socket
        server.shutdown()
        try:
            if path.stat().st_ino == inode:
                path.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()

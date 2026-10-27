#!/usr/bin/env python3
"""PEACE desktop agent: shows the server's notifications natively, carries out their actions,
keeps a copy of the course materials in the Obsidian vault, runs push-to-talk (hold SUPER+Y) and carries
out the desktop actions the assistant planned (peace_actions.py: windows, files, apps, Brave tabs).

Standard library only, so it runs on the system Python. Notifications go through notify-send, i.e.
D-Bus org.freedesktop.Notifications (served here by the desktop shell), silent by default. When the
server is unreachable it keeps retrying; whatever was due meanwhile is still pending on the server and
appears on reconnect (expired reminders are dropped there).
"""

try:
    import fcntl  # Linux keyboards; Windows reads keys in peace_windows.py
except ImportError:
    fcntl = None
import hashlib
import html
import json
import logging
import os
import queue
import re
import select
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import peace_actions as actions  # noqa: E402

API = os.environ.get("PEACE_URL", "http://127.0.0.1:8420").rstrip("/")
DASHBOARD = os.environ.get("PEACE_DASHBOARD", API).rstrip("/")
POLL_SECONDS = int(os.environ.get("PEACE_POLL_SECONDS", "30"))  # heartbeat and settings
NOTIFY_SECONDS = 3  # pending notifications: a local request, so a short wait costs nothing
ICON = os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/peace.svg")
LABELS = {"done": "Concluída", "snooze": "Adiar", "skip": "Saltar", "replan": "Replanear"}
VAULT = Path(os.environ.get("PEACE_VAULT", "~/Peace Vault")).expanduser()
STATE = Path(os.environ.get("PEACE_STATE", "~/.local/state/peace/materials.json")).expanduser()
MIRROR_MINUTES = int(os.environ.get("PEACE_MIRROR_MINUTES", "30"))
MAX_BYTES = int(os.environ.get("PEACE_MATERIALS_MAX_MB", "200")) * 1024 * 1024
RECEIPTS = STATE.with_name("notification-receipts.json")
NATIVE_ACK_TIMEOUT = 5

log = logging.getLogger("peace-desktop")


def _request(method: str, path: str, body: dict | None = None) -> urllib.request.Request:
    return urllib.request.Request(
        API + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['PEACE_TOKEN']}", "Content-Type": "application/json"},
    )


def call(method: str, path: str, body: dict | None = None):
    with urllib.request.urlopen(_request(method, path, body), timeout=15) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside_vault(relative: str) -> Path | None:
    """Target for a server-given path, or None if it would leave the vault or touch a hidden folder."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p.startswith(".") for p in parts):
        return None
    target = (VAULT / relative).resolve()
    return target if target.is_relative_to(VAULT.resolve()) else None


def download(material_id: int, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.peace-part")  # hidden: Obsidian ignores it
    try:
        with urllib.request.urlopen(_request("GET", f"/api/v1/materials/{material_id}/file"), timeout=300) as r, partial.open("wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def mirror_materials() -> int:
    """Copy new or changed Moodle files into the vault. Never overwrites a file it did not write or that
    was edited since (the new version goes next to it), never deletes. Returns files written."""
    if not VAULT.is_dir():
        log.warning("vault %s not found; materials not copied", VAULT)
        return 0
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    written = 0
    for m in call("GET", "/api/v1/materials?kind=file") or []:
        # Keyed by vault path, not database id, so a re-deployed server does not duplicate every file.
        key = m["vault_path"] or ""
        known = state.get(key)
        if (known and known["modified_at"] == m["modified_at"]) or (m["size"] or 0) > MAX_BYTES:
            continue
        target = _inside_vault(m["vault_path"] or "")
        if target is None:
            log.warning("unsafe path for material %s skipped: %r", m["id"], m["vault_path"])
            continue
        if target.exists() and (not known or known.get("path") != str(target) or _sha256(target) != known["sha256"]):
            target = target.with_name(f"{target.stem} (PEACE {(m['modified_at'] or '')[:10]}){target.suffix}")
            if target.exists():
                continue
        try:
            download(m["id"], target)
        except urllib.error.HTTPError as e:
            if e.code == 404:  # the server has not fetched this file yet: the next round copies it
                continue
            raise
        state[key] = {"path": str(target), "modified_at": m["modified_at"], "sha256": _sha256(target)}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.with_suffix(".tmp").write_text(json.dumps(state, indent=1))
        os.replace(STATE.with_suffix(".tmp"), STATE)
        written += 1
    return written


NOTES_DIR = "07 - Generated/Graphify"


def mirror_notes() -> int:
    """Keep Graphify's generated notes in the vault's generated folder. Updates or removes only notes
    this agent wrote and that are unchanged since; never touches the user's own files there."""
    if not VAULT.is_dir():
        return 0
    state_file = STATE.with_name("graphify-notes.json")
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    remote = {n["path"]: n["sha256"] for n in call("GET", "/api/v1/knowledge/notes") or []}
    written = 0
    for path, sha in remote.items():
        target = _inside_vault(f"{NOTES_DIR}/{path}")
        if target is None or state.get(path) == sha:
            continue
        if target.exists() and (path not in state or _sha256(target) != state[path]):
            continue  # someone else's file, or edited by the user
        request = _request("GET", f"/api/v1/knowledge/notes/file?path={urllib.parse.quote(path)}")
        with urllib.request.urlopen(request, timeout=30) as r:
            data = r.read()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_name(f".{target.name}.peace-part").write_bytes(data)
        os.replace(target.with_name(f".{target.name}.peace-part"), target)
        state[path] = hashlib.sha256(data).hexdigest()
        written += 1
    for path in [p for p in state if p not in remote]:
        target = _inside_vault(f"{NOTES_DIR}/{path}")
        if target and target.exists() and _sha256(target) == state[path]:
            target.unlink()
        del state[path]
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=1))
    return written


def mirror_loop() -> None:
    while True:
        try:
            if n := mirror_materials():
                log.info("copied %d course files into the vault", n)
            if n := mirror_notes():
                log.info("updated %d graph notes in the vault", n)
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.warning("materials not copied this time: %s", e)
        time.sleep(MIRROR_MINUTES * 60)


def command(n: dict) -> list[str]:
    """notify-send arguments. The body is escaped: it may hold scraped text and the server renders markup."""
    args = [
        "notify-send",
        "--app-name=PEACE",
        f"--icon={ICON}",
        "--hint=boolean:suppress-sound:true",
        f"--urgency={'critical' if n['urgent'] else 'normal'}",
        f"--expire-time={60000 if n['urgent'] else 15000}",
        "--wait",
        "--action=default=Abrir",
    ]
    args += [f"--action={a}={LABELS[a]}" for a in n["actions"] if a in LABELS]
    # notify-send unescapes backslashes before passing body markup to the daemon.
    return args + ["--", n["title"], html.escape(n["body"]).replace("\\", "\\\\")]


def handle(n: dict, action: str) -> None:
    if action == "default":
        subprocess.Popen(["xdg-open", DASHBOARD + n["url"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif action == "done" and n["task_id"]:
        call("PATCH", f"/api/v1/tasks/{n['task_id']}", {"status": "COMPLETED"})
    elif action == "snooze":
        call("POST", f"/api/v1/notifications/{n['id']}/snooze", {"minutes": 10 if n["kind"] == "session" else 60})
    elif action == "skip" and n["session_id"]:
        call("PATCH", f"/api/v1/sessions/{n['session_id']}", {"status": "skipped"})
    elif action == "replan":
        call("POST", f"/api/v1/plan/day?date={date.today() + timedelta(days=1)}")


def deliver(n: dict) -> bool:
    """A native ID confirms daemon acceptance, never human-observed popup visibility."""
    key = f"{n['id']}:{n.get('deliver_after', n.get('created_at', ''))}:{n.get('expires_at', '')}"
    waiter = None
    try:
        receipts = json.loads(RECEIPTS.read_text()) if RECEIPTS.exists() else {}
        receipt = receipts.get(key)
        if not receipt:
            read_fd, write_fd = os.pipe()
            try:
                args = command(n)
                args.insert(-3, f"--id-fd={write_fd}")
                proc = subprocess.Popen(args,
                                        pass_fds=(write_fd,), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                os.close(write_fd)
                write_fd = None
                ready, _, _ = select.select([read_fd], [], [], NATIVE_ACK_TIMEOUT)
                native_id = os.read(read_fd, 64).decode().strip() if ready else ""
                if not native_id.isdigit() or int(native_id) <= 0:
                    proc.kill()
                    proc.communicate()
                    raise OSError("notification daemon did not return an acceptance ID")
            finally:
                os.close(read_fd)
                if write_fd is not None:
                    os.close(write_fd)

            def wait_for_action() -> None:
                try:
                    action = (proc.communicate(timeout=900)[0] or "").strip()
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    return
                if action:
                    try:
                        handle(n, action)
                        log.info("notification %s: %s", n["id"], action)
                    except (urllib.error.URLError, OSError) as e:
                        log.warning("action %s failed: %s", action, e)

            waiter = threading.Thread(target=wait_for_action, daemon=True)
            receipt = {"native_id": int(native_id), "at": time.time()}
            # Reminders expire within two days; retain receipts a week across agent/API restarts.
            receipts = {k: v for k, v in receipts.items() if v["at"] > time.time() - 7 * 86400}
            receipts[key] = receipt
            RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
            temp = RECEIPTS.with_suffix(".tmp")
            temp.write_text(json.dumps(receipts))
            os.replace(temp, RECEIPTS)
        call("POST", f"/api/v1/notifications/{n['id']}/delivered")
        report_notification_delivery(notification_id=n["id"], native_id=receipt["native_id"], result="accepted")
        return True
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning("notification %s not acknowledged: %s", n["id"], e)
        report_notification_delivery(notification_id=n["id"], result="error")
        return False
    finally:
        if waiter:
            waiter.start()  # process actions after acknowledgement, so a quick snooze is not overwritten


def notification_diagnostics() -> dict:
    """Probe the actual session bus without copying its addresses or notification text."""
    status = {"notify_send": bool(shutil.which("notify-send")), "session_bus": bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS")),
              "daemon_available": False, "daemon": None}
    if status["session_bus"] and shutil.which("busctl"):
        try:
            proc = subprocess.run(["busctl", "--user", "--auto-start=no", "--timeout=2", "call", "org.freedesktop.Notifications",
                                   "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "GetServerInformation"],
                                  capture_output=True, text=True, timeout=3)
            status["daemon_available"] = proc.returncode == 0
            status["daemon"] = proc.stdout.strip()[:250] if proc.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            pass
    return status


def report_notification_delivery(**values) -> None:
    try:
        call("POST", "/api/v1/desktop/notifications/status", values)
    except (urllib.error.URLError, OSError, ValueError):
        pass  # a diagnostic failure must never block queue delivery


# --- voice: hold the key, speak, release ---------------------------------------------------------
# Hyprland calls this script ("ptt start" on press, "ptt stop" on release); those calls reach the running
# agent over a Unix socket. Speech is turned into text by VoiceStudio (local Whisper), PEACE answers, and
# the reply is spoken (when a fast enough voice is configured) or shown as a notification.

VOICESTUDIO = os.environ.get("PEACE_VOICESTUDIO", "http://127.0.0.1:3900").rstrip("/")
OLLAMA = os.environ.get("PEACE_OLLAMA", "http://127.0.0.1:11434").rstrip("/")
LLM_MODEL = os.environ.get("PEACE_LLM_MODEL", "gpt-oss:20b")  # until the server says which model it uses
PTT = os.environ.get("PEACE_PTT", "SUPER + Y")  # Hyprland key combo, as written in hyprland.lua
TTS = os.environ.get("PEACE_TTS", "off")  # a VoiceStudio speech engine id (supertonic3), or "off": notifications
TTS_VOICE = os.environ.get("PEACE_TTS_VOICE", "F1")  # Supertonic's F1..F5
VOICES = {"supertonic3": [f"F{i}" for i in range(1, 6)]}  # per engine, for the Settings picker; female voices only
PTT_RE = re.compile(r"^(SUPER|ALT|CTRL|SHIFT)( \+ (SUPER|ALT|CTRL|SHIFT))* \+ [A-Z0-9]$")  # it ends up in Lua code
# Live voice settings: env values until the server's Settings say otherwise (applied on every heartbeat).
DEVICE_RE = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9_.:@/-]{0,199})?$")
settings = {"ptt": PTT, "speak": TTS != "off", "voice": TTS_VOICE, "language": "en", "model": LLM_MODEL,
            "input_device": os.environ.get("PEACE_INPUT_DEVICE", ""), "output_device": os.environ.get("PEACE_OUTPUT_DEVICE", "")}
# Private per-user directory only; never a guessable path in /tmp (voice is off without it).
RUNTIME = Path(os.environ["XDG_RUNTIME_DIR"]) if os.environ.get("XDG_RUNTIME_DIR") else None
SOCKET = RUNTIME / "peace-voice.sock" if RUNTIME else None
RECORDING = RUNTIME / "peace-voice.wav" if RUNTIME else None
MAX_SECONDS = 30  # last resort when the keyboards cannot be read and Hyprland missed the release
# Linux key codes (input-event-codes.h) for the push-to-talk combo, read straight from the keyboards.
KEYCODES = {c: n for row, first in (("1234567890", 2), ("QWERTYUIOP", 16), ("ASDFGHJKL", 30), ("ZXCVBNM", 44))
            for n, c in enumerate(row, first)}
MOD_KEYCODES = {"SUPER": (125, 126), "SHIFT": (42, 54), "CTRL": (29, 97), "ALT": (56, 100)}  # left, right
EVIOCGKEY = (2 << 30) | (96 << 16) | (ord("E") << 8) | 0x18  # read the 96-byte "keys down" bitmap
voice_diagnostics = {"inputs": [], "outputs": [], "error": None, "ptt_bound": False, "latency": {},
                     "capture_available": False, "playback_available": False, "stt_available": False}


def audio_devices() -> dict:
    """PulseAudio's PipeWire bridge exposes stable node names; enumeration never records audio."""
    result = {"inputs": [], "outputs": [], "error": None}
    try:
        for kind, key in (("sources", "inputs"), ("sinks", "outputs")):
            proc = subprocess.run(["pactl", "--format=json", "list", kind], capture_output=True, text=True, timeout=3)
            if proc.returncode:
                raise OSError("PipeWire/PulseAudio indisponível")
            result[key] = [{"id": d["name"], "name": d.get("description") or d["name"]}
                           for d in json.loads(proc.stdout) if DEVICE_RE.fullmatch(d.get("name", ""))
                           and d.get("name") and not (key == "inputs" and d["name"].endswith(".monitor"))]
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        result["error"] = "Não foi possível consultar os dispositivos PipeWire/PulseAudio."
    return result


def record_command(path: Path, raw: bool = False, input_device: str | None = None) -> list[str]:
    args = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"]
    if raw:
        args += ["--raw"]
    selected = settings["input_device"] if input_device is None else input_device
    if selected:
        args += ["--target", selected]
    return args + [str(path)]


def _up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def capabilities() -> dict:
    """What this desktop can really do right now (the dashboard shows exactly this)."""
    engine = _up(VOICESTUDIO + "/health")
    voice_diagnostics["stt_available"] = engine
    voice_diagnostics["capture_available"] = bool(RUNTIME and shutil.which("pw-record") and voice_diagnostics["inputs"]
        and (not settings["input_device"] or any(d["id"] == settings["input_device"] for d in voice_diagnostics["inputs"])))
    voice_diagnostics["playback_available"] = bool(RUNTIME and shutil.which("pw-play") and voice_diagnostics["outputs"]
        and (not settings["output_device"] or any(d["id"] == settings["output_device"] for d in voice_diagnostics["outputs"])))
    voice = engine and voice_diagnostics["capture_available"] and voice_diagnostics["ptt_bound"]
    speaking = TTS != "off" and settings["speak"]
    name = ("STT pt" + (f" · {TTS} {settings['voice']}" if speaking else ", respostas escritas")) if voice else None
    return {"voice": voice, "voice_name": name, "ptt": settings["ptt"].replace(" ", ""), "llm": _up(OLLAMA + "/api/tags"),
            "voices": VOICES.get(TTS, []) if engine else [], "audio": voice_diagnostics, "actions": actions.availability()}


def _hyprctl(*args: str) -> bool:
    return subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=5).stdout.strip() == "ok"


PTT_TAG = "PEACE push-to-talk"  # Lua binds show only an opaque function id; this description marks PEACE's own


def ptt_conflict(combo: str) -> bool:
    """Do not unbind another application's shortcut to install voice activation."""
    bits = {"SHIFT": 1, "CTRL": 4, "ALT": 8, "SUPER": 64}
    *mods, key = combo.split(" + ")
    mask = sum(bits[m] for m in set(mods))
    try:
        proc = subprocess.run(["hyprctl", "-j", "binds"], capture_output=True, text=True, timeout=5)
        return any(b.get("modmask") == mask and str(b.get("key", "")).upper() == key
                   and "peace_desktop.py ptt " not in b.get("arg", "") and b.get("description") != PTT_TAG for b in json.loads(proc.stdout))
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        log.warning("could not inspect Hyprland shortcuts; refusing to replace existing bindings")
        return True


def bind_ptt(combo: str | None = None, previous: str | None = None) -> bool:
    """Hold-to-talk in Hyprland, at runtime only (no config file is edited; re-applied on every start).
    Lua configs (Hyprland 0.56+) take `hyprctl eval`; older ones the legacy `keyword` form."""
    combo = combo or settings["ptt"]
    if not PTT_RE.fullmatch(combo) or (previous and not PTT_RE.fullmatch(previous)):
        log.warning("refusing push-to-talk combo %r", combo)
        return False
    if ptt_conflict(combo):
        voice_diagnostics["error"] = "Atalho ocupado ou Hyprland indisponível. Escolhe outra combinação."
        return False
    run = f"{sys.executable} {Path(__file__).resolve()} ptt"
    unbind = {combo}
    if previous and not ptt_conflict(previous):
        unbind.add(previous)
    lua = [f'hl.unbind("{c}")' for c in unbind] + [
        f'hl.bind("{combo}", hl.dsp.exec_cmd("{run} start"), {{ description = "{PTT_TAG}" }})',
        f'hl.bind("{combo}", hl.dsp.exec_cmd("{run} stop"), {{ release = true, description = "{PTT_TAG}" }})']
    *mods, key = [p.strip() for p in combo.split("+")]
    legacy = f"{' '.join(mods)},{key}"
    try:
        if all(_hyprctl("eval", step) for step in lua):
            return True
        unbindings = [("unbind", f"{' '.join(c.split(' + ')[:-1])},{c.split(' + ')[-1]}") for c in unbind]
        return all(_hyprctl("keyword", *step) for step in [*unbindings, ("bind", f"{legacy},exec,{run} start"), ("bindr", f"{legacy},exec,{run} stop")])
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("push-to-talk not bound: %s", e)
        return False


def apply_voice_settings(prefs: dict) -> None:
    """Settings from the dashboard: rebind the key when it changed, use the chosen voice."""
    wanted = prefs.get("ptt", "")
    if PTT_RE.fullmatch(wanted) and wanted not in (settings["ptt"], settings.get("failed")):
        if bind_ptt(wanted, previous=settings["ptt"]):
            log.info("push-to-talk moved to %s", wanted)
            settings["ptt"] = wanted
            voice_diagnostics["ptt_bound"] = True
        else:  # don't retry it on every poll; the old binding may be gone, so restore it
            settings["failed"] = wanted
            voice_diagnostics["ptt_bound"] = bind_ptt(settings["ptt"])
    settings["speak"] = bool(prefs.get("speak", settings["speak"]))
    if prefs.get("voice") in VOICES.get(TTS, []):
        settings["voice"] = prefs["voice"]
    if prefs.get("language") in ("pt", "en"):
        settings["language"] = prefs["language"]
    for key in ("input_device", "output_device"):
        if key in prefs and isinstance(prefs[key], str) and DEVICE_RE.fullmatch(prefs[key]):
            settings[key] = prefs[key]


def multipart(fields: dict[str, str], file_field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode() + data + b"\r\n")
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def transcribe(wav: Path) -> str:
    body, kind = multipart({"model": "whisper-1", "language": "pt"}, "file", "fala.wav", wav.read_bytes())
    request = urllib.request.Request(VOICESTUDIO + "/v1/audio/transcriptions", data=body, method="POST", headers={"Content-Type": kind})
    with urllib.request.urlopen(request, timeout=60) as r:
        return json.loads(r.read()).get("text", "").strip()


def for_speech(text: str) -> str:
    """Read times the way people say them: "às 17:00" -> "às 17 horas", "19:40" -> "19 e 40"."""
    text = re.sub(r"\b(\d{1,2}):00\b", lambda m: f"{int(m[1])} {'hora' if int(m[1]) == 1 else 'horas'}", text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: f"{int(m[1])} e {int(m[2])}", text)
    hours = lambda n: f"{n} {'hora' if n == 1 else 'horas'}"  # noqa: E731
    text = re.sub(r"\b(\d+) h (\d+) min\b", lambda m: f"{hours(int(m[1]))} e {int(m[2])} minutos" if int(m[2]) else hours(int(m[1])), text)
    return text.replace("«", "").replace("»", "")


def keys_held(combo: str) -> bool | None:
    """Are all keys of the combo still down? None when no keyboard can be read (user not in the input group)."""
    *mods, key = [p.strip() for p in combo.split("+")]
    wanted = [MOD_KEYCODES[m] for m in mods] + [(KEYCODES[key],)]
    state, seen = bytearray(96), False
    for device in Path("/dev/input/by-id").glob("*-event-kbd"):
        try:
            with open(device, "rb") as f:
                keys = bytearray(96)
                fcntl.ioctl(f, EVIOCGKEY, keys)
        except OSError:
            continue
        seen, state = True, bytearray(a | b for a, b in zip(state, keys))
    if not seen:
        return None
    return all(any(state[c // 8] >> (c % 8) & 1 for c in group) for group in wanted)


def warm_model() -> None:
    """Load the model while the user speaks: Ollama unloads it after 10 idle minutes and loading takes ~11 s."""
    body = {"model": settings["model"], "keep_alive": "10m", "options": {"num_ctx": 8192}}  # the server's num_ctx, or it reloads
    request = urllib.request.Request(OLLAMA + "/api/generate", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps(body).encode())
    try:
        with urllib.request.urlopen(request, timeout=60) as r:
            r.read()
    except (urllib.error.URLError, OSError, ValueError):
        pass  # the turn itself reports a missing model


def to_english(text: str) -> str | None:
    """PEACE's replies are Portuguese; the speech engine sounds far better in English. None: speak Portuguese."""
    body = {"model": settings["model"], "stream": False, "think": "low", "keep_alive": "10m",
            "options": {"temperature": 0, "num_ctx": 8192},  # the server's num_ctx: a different one reloads the model
            "messages": [{"role": "system", "content": "Translate the user message from European Portuguese into natural "
                                                      "spoken English. Reply with the translation only."},
                         {"role": "user", "content": text.replace("«", "").replace("»", "")}]}
    request = urllib.request.Request(OLLAMA + "/api/chat", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps(body).encode())
    try:
        with urllib.request.urlopen(request, timeout=20) as r:
            return json.loads(r.read())["message"]["content"].strip() or None
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return None


def speak(text: str, voice_name: str | None = None, output_device: str | None = None, language: str | None = None) -> None:
    english = to_english(text) if (language or settings["language"]) == "en" else None
    body = {"model": TTS, "input": english or for_speech(text), "voice": settings["voice"] if voice_name is None else voice_name,
            "language": "en" if english else "pt", "response_format": "wav"}
    request = urllib.request.Request(VOICESTUDIO + "/v1/audio/speech", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps(body).encode())
    audio = RUNTIME / "peace-reply.wav"
    try:
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=120) as r:
            audio.write_bytes(r.read())
        voice_diagnostics["latency"]["tts_ms"] = round((time.monotonic() - started) * 1000)
        set_state("speaking")  # synthesis can take seconds; speaking means playback really starts
        started = time.monotonic()
        play(audio, settings["output_device"] if output_device is None else output_device)
        voice_diagnostics["latency"]["playback_ms"] = round((time.monotonic() - started) * 1000)
    finally:
        audio.unlink(missing_ok=True)


def play(audio: Path, device: str) -> None:
    """Play a WAV file to the end on the chosen output ("" = the default one)."""
    args = ["pw-play"] + (["--target", device] if device else [])
    subprocess.run(args + [str(audio)], check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify_reply(heard: str, reply: str) -> None:
    subprocess.Popen(["notify-send", "--app-name=PEACE", f"--icon={ICON}", "--hint=boolean:suppress-sound:true", "--",
                      f"«{heard}»"[:120], html.escape(reply).replace("\\", "\\\\")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


state_updates = queue.Queue(maxsize=1)
state_worker_lock = threading.Lock()
state_worker = None


def _state_loop() -> None:
    while True:
        state = state_updates.get()
        try:
            call("POST", "/api/v1/assistant/state", {"state": state})
        except (urllib.error.URLError, OSError, ValueError):
            pass  # the orb must never block capture or keyboard release
        finally:
            state_updates.task_done()


def set_state(state: str) -> None:
    """One ordered network writer; a slow listening request cannot arrive after idle."""
    global state_worker
    with state_worker_lock:
        if state_worker is None:
            state_worker = threading.Thread(target=_state_loop, daemon=True)
            state_worker.start()
        try:
            state_updates.put_nowait(state)
        except queue.Full:
            try:
                state_updates.get_nowait()
                state_updates.task_done()
            except queue.Empty:
                pass
            state_updates.put_nowait(state)  # obsolete unsent states never replay after an outage


def has_audio(path: Path) -> bool:
    """A readable WAV with at least 0.1 s of sound. pw-record 1.6 exits 1 after SIGINT even though the file is
    complete, so the file itself is the evidence, not the exit code."""
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() >= w.getframerate() // 10
    except (OSError, EOFError, wave.Error):
        return False


class Voice:
    """One push-to-talk turn at a time: record while held, then transcribe, ask and answer."""

    def __init__(self) -> None:
        self.recorder: subprocess.Popen | None = None
        self.started = 0.0
        self.busy = False  # an answer is still being prepared or spoken: a new press waits for it
        self.lock = threading.Lock()

    def start(self) -> None:
        with self.lock:
            if self.recorder or self.busy:
                return
            # Record first: the state update goes over the network and must not clip the first words.
            try:
                RECORDING.unlink(missing_ok=True)
                self.recorder = self.open_recorder()
            except OSError:
                voice_diagnostics["error"] = "Não foi possível iniciar a captura. Verifica PipeWire e o microfone selecionado."
                notify_reply("Voz indisponível", voice_diagnostics["error"])
                return
            self.started = time.monotonic()
            started = self.started
        log.info("push-to-talk: key down, microphone open")  # never what was said
        threading.Thread(target=self.monitor_capture, args=(self.recorder,), daemon=True).start()
        threading.Thread(target=self.watch_release, args=(started,), daemon=True).start()
        threading.Thread(target=warm_model, daemon=True).start()
        threading.Timer(MAX_SECONDS, lambda: self.started == started and self.stop()).start()

    def open_recorder(self):
        return subprocess.Popen(record_command(RECORDING), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close_recorder(self, proc) -> None:
        """Stop recording; RECORDING then holds the complete WAV."""
        try:
            proc.send_signal(signal.SIGINT)  # pw-record finishes the WAV header on SIGINT
        except ProcessLookupError:
            pass  # exited between the monitor's poll and this release
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def watch_release(self, started: float) -> None:
        """Hyprland misses the key release when SUPER goes up first; the keyboard itself never does."""
        time.sleep(0.15)
        while self.started == started and self.recorder:
            held = keys_held(settings["ptt"])
            if held is None:
                return  # keyboards unreadable: Hyprland's release or MAX_SECONDS ends the turn
            if not held:
                self.stop()
                return
            time.sleep(0.05)

    def monitor_capture(self, proc: subprocess.Popen) -> None:
        listening = False
        while True:
            with self.lock:
                if self.recorder is not proc:
                    return
                if proc.poll() is not None:
                    self.recorder = None
                    voice_diagnostics["error"] = "A captura terminou inesperadamente. Verifica o microfone e PipeWire."
                    RECORDING.unlink(missing_ok=True)
                    set_state("idle")
                    notify_reply("Voz indisponível", voice_diagnostics["error"])
                    return
                if not listening and RECORDING.exists() and RECORDING.stat().st_size > 44:
                    set_state("listening")  # emitted only after real PCM reached the file
                    voice_diagnostics["error"] = None
                    listening = True
            time.sleep(0.05)

    def stop(self) -> None:
        with self.lock:
            proc, self.recorder = self.recorder, None
            if not proc:
                return
            self.close_recorder(proc)
            held = time.monotonic() - self.started
            self.busy = True  # stays reserved until cleanup and idle publication finish
        log.info("push-to-talk: key up after %.1f s, microphone closed", held)
        if held < 0.4:  # a tap, not speech
            if RECORDING:
                RECORDING.unlink(missing_ok=True)
            with self.lock:
                set_state("idle")
                self.busy = False
            return
        if not has_audio(RECORDING):
            log.warning("push-to-talk: no usable audio (recorder exit %s)", getattr(proc, "returncode", None))
            RECORDING.unlink(missing_ok=True)
            with self.lock:
                set_state("idle")
                self.busy = False
            notify_reply("Voz indisponível", "O microfone não forneceu áudio. Verifica o dispositivo em Definições → Voz.")
            return
        threading.Thread(target=self.answer, daemon=True).start()

    def answer(self) -> None:
        try:
            voice_diagnostics["latency"] = {}
            set_state("thinking")
            started = time.monotonic()
            heard = transcribe(RECORDING)
            voice_diagnostics["latency"]["stt_ms"] = round((time.monotonic() - started) * 1000)
            if not heard:
                notify_reply("…", f"Não percebi. Mantém {settings['ptt']} premido enquanto falas.")
                return
            started = time.monotonic()
            answer = call("POST", "/api/v1/assistant/ask", {"text": heard[:500], "source": "voice"})
            reply = answer["reply"]
            if (answer.get("job") or {}).get("state") == "pending":  # say what really happened, not what was planned
                reply = wait_for_job(answer["job"]["id"])
            voice_diagnostics["latency"]["agent_ms"] = round((time.monotonic() - started) * 1000)
            if TTS != "off" and settings["speak"]:
                speak(reply)
            else:
                notify_reply(heard, reply)
            voice_diagnostics["error"] = None
        except Exception as e:  # any failure must reach the user, not vanish in a thread
            log.warning("voice turn failed: %s", type(e).__name__)
            voice_diagnostics["error"] = "Falha de captura, transcrição, servidor ou reprodução; usa os testes de Voz."
            notify_reply("…", "A voz não está disponível agora (VoiceStudio ou o servidor não respondem).")
        finally:
            if RECORDING:
                RECORDING.unlink(missing_ok=True)
            with self.lock:
                set_state("idle")
                self.busy = False


def run_voice_test(job: dict, voice: Voice) -> None:
    """Only runs after the explicit Settings test action; never forwards test speech to the agent."""
    if job.get('capture_before') and datetime.now(UTC) >= datetime.fromisoformat(job['capture_before']):
        raise RuntimeError('O consentimento de teste expirou.')
    with voice.lock:
        if voice.busy or voice.recorder:
            raise RuntimeError("A voz está ocupada; tenta novamente.")
        voice.busy = True
    proc = None
    telemetry = None
    telemetry_stop = threading.Event()
    levels = queue.Queue(maxsize=1)
    audio = RUNTIME / "peace-test.wav"
    path = f"/api/v1/desktop/voice-test/{job['id']}"

    def report_levels() -> None:
        while not telemetry_stop.is_set():
            try:
                level = levels.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                call("POST", path, {"state": "running", "message": "A captar durante 5 segundos…", "level": level})
            except (urllib.error.URLError, OSError, ValueError):
                return

    try:
        options = job["settings"]
        for key in ("input_device", "output_device"):
            if not DEVICE_RE.fullmatch(options.get(key, "")):
                raise ValueError("invalid device")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", options.get("voice", "default")):
            raise ValueError("invalid voice")
        if job["action"] == "tts":
            if TTS == "off":
                raise RuntimeError("Motor TTS desligado no agente (PEACE_TTS).")
            voice_diagnostics["latency"] = {}
            speak("Olá. Sou a PEACE. Amanhã tens uma avaliação às dezassete horas. Como correu a revisão?",
                  voice_name=options.get("voice", "F1"), output_device=options.get("output_device", ""),
                  language="pt" if options.get("language") == "pt" else "en")
            call("POST", path, {"state": "done", "message": "Reprodução terminada. Confirma por ouvido o som e o sotaque.", "level": 0})
            return
        proc = subprocess.Popen(record_command(Path("-"), raw=True, input_device=options.get("input_device", ""))[:-1] + ["--sample-count", "80000", "-"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        telemetry = threading.Thread(target=report_levels, daemon=True)
        telemetry.start()
        chunks = []
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            if not select.select([proc.stdout], [], [], 0.2)[0]:
                continue
            chunk = os.read(proc.stdout.fileno(), 6400)
            if not chunk:
                break
            chunks.append(chunk)
            samples = struct.unpack(f"<{len(chunk) // 2}h", chunk[:len(chunk) // 2 * 2])
            level = min(1.0, (sum(x * x for x in samples) / max(1, len(samples))) ** 0.5 / 32768)
            if levels.full():
                try:
                    levels.get_nowait()
                except queue.Empty:
                    pass
            levels.put_nowait(level)
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=3)
        telemetry_stop.set()
        telemetry.join(timeout=16)  # at most one in-flight HTTP request, after capture has stopped
        if telemetry.is_alive() or sum(map(len, chunks)) != 160000:
            raise RuntimeError("Captação incompleta. Verifica o dispositivo e PipeWire.")
        if job["action"] == "stt":
            with wave.open(str(audio), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"".join(chunks))
            heard = transcribe(audio)
            # Test transcripts stay transient; the backend stores only test status, never speech.
            message = "Transcrição recebida; confirma o texto na notificação local." if heard else "Nenhuma fala reconhecida."
            if heard:
                notify_reply("Teste de transcrição (não guardado)", heard)
        else:
            message = "Teste de entrada terminado. O medidor mostra a amplitude real captada."
        call("POST", path, {"state": "done", "message": message, "level": 0})
    finally:
        telemetry_stop.set()
        if proc:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()
        if telemetry:
            telemetry.join(timeout=16)
        audio.unlink(missing_ok=True)
        with voice.lock:
            set_state("idle")
            voice.busy = False


def voice_test_loop(voice: Voice) -> None:
    last = None
    refreshed = 0.0
    while True:
        try:
            if time.monotonic() - refreshed > 30:
                voice_diagnostics.update(audio_devices())
                refreshed = time.monotonic()
            job = call("GET", "/api/v1/desktop/voice-test")
            if job and job["id"] != last:
                last = job["id"]
                try:
                    run_voice_test(job, voice)
                except Exception as e:
                    log.warning("voice test failed: %s", type(e).__name__)
                    call("POST", f"/api/v1/desktop/voice-test/{last}",
                         {"state": "error", "message": str(e) if isinstance(e, RuntimeError) else
                          "Teste falhou. Verifica o dispositivo, PipeWire e VoiceStudio.", "level": 0})
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            pass  # the main loop reports connectivity; diagnostics retry independently
        time.sleep(1)


# --- desktop actions: the plan comes from the server, every step is checked and run here ---------------


def run_job(job: dict) -> None:
    path = f"/api/v1/desktop/jobs/{job['id']}/progress"
    cancel = {"requested": False}

    def progress(results: list[dict]) -> None:
        try:
            cancel["requested"] = bool(call("POST", path, {"results": results})["cancel"])
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            pass  # the final report below retries; what already ran stays reported there

    results, cancelled = actions.run_plan(job.get("steps"), bool(job.get("confirmed")), str(job["id"]),
                                          lambda: cancel["requested"], progress)
    for attempt in range(3):
        try:
            call("POST", path, {"results": results, "final": not cancelled, "cancelled": cancelled})
            return
        except urllib.error.HTTPError:
            return  # the server gave up on this job (expired or replaced); nothing to retry
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(1 + attempt)
    log.warning("result of desktop job %s not reported", job["id"])


def jobs_loop() -> None:
    while True:
        try:
            if job := call("GET", "/api/v1/desktop/jobs/next"):
                run_job(job)
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            pass  # the main loop reports connectivity
        time.sleep(1)


def wait_for_job(job_id: str, seconds: float = 120) -> str:
    """The summary of a job the voice turn started, once the jobs loop has run it."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            job = call("GET", f"/api/v1/desktop/jobs/{job_id}")
            if job["state"] not in ("pending", "running"):
                return job["summary"] or "Terminei, mas não recebi o resumo."
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            pass
        time.sleep(0.5)
    return "A ação no PC ainda está a decorrer; vê o resultado no painel."


def serve_voice(voice: Voice) -> None:
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.request.settimeout(2)  # a client that never sends must not block push-to-talk
            command = self.rfile.readline(16).decode(errors="replace").strip()
            if command == "start":
                voice.start()
            elif command == "stop":
                voice.stop()

    SOCKET.unlink(missing_ok=True)
    server = socketserver.UnixStreamServer(str(SOCKET), Handler)
    os.chmod(SOCKET, 0o600)  # only this user can trigger recording
    server.serve_forever()


def send_ptt(command: str) -> None:
    """Entry point for the Hyprland binding: forward to the running agent."""
    if not SOCKET:
        raise SystemExit("XDG_RUNTIME_DIR is not set")
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(2)
        s.connect(str(SOCKET))
        s.sendall(command.encode() + b"\n")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log.info("polling %s every %ss (notifications every %ss)", API, POLL_SECONDS, NOTIFY_SECONDS)
    threading.Thread(target=mirror_loop, daemon=True).start()
    threading.Thread(target=jobs_loop, daemon=True).start()
    if RUNTIME:
        voice = Voice()
        threading.Thread(target=serve_voice, args=(voice,), daemon=True).start()
        threading.Thread(target=voice_test_loop, args=(voice,), daemon=True).start()
        voice_diagnostics["ptt_bound"] = bind_ptt()
        log.info("push-to-talk on %s: %s", PTT, "bound" if voice_diagnostics["ptt_bound"] else "NOT bound")
    else:
        log.warning("XDG_RUNTIME_DIR not set: push-to-talk disabled")
    failures, next_beat = 0, 0.0
    while True:
        try:
            if time.monotonic() >= next_beat:
                next_beat = time.monotonic() + POLL_SECONDS
                try:  # voice must never stop notifications
                    apply_voice_settings(call("GET", "/api/v1/preferences/voice") or {})
                    # The server's model, so translation and warm-up never load a second one.
                    settings["model"] = (call("GET", "/api/v1/assistant/models") or {}).get("using") or settings["model"]
                    call("POST", "/api/v1/desktop/heartbeat", {"capabilities": capabilities()})
                except (urllib.error.URLError, OSError, ValueError) as e:
                    log.warning("voice settings/heartbeat failed: %s", e)
                report_notification_delivery(**notification_diagnostics())
            for n in call("GET", "/api/v1/notifications/pending") or []:
                deliver(n)
            if failures:
                log.info("server reachable again")
            failures = 0
        except urllib.error.HTTPError as e:
            log.error("server refused the request (%s); check PEACE_TOKEN", e.code)
        except (urllib.error.URLError, OSError, ValueError) as e:
            failures += 1
            if failures == 1 or failures % 120 == 0:
                log.warning("server unreachable (%s); retrying", e)
        time.sleep(NOTIFY_SECONDS)


if __name__ == "__main__":
    if sys.argv[1:2] == ["ptt"] and sys.argv[2:3] in (["start"], ["stop"]):
        send_ptt(sys.argv[2])
    else:
        main()

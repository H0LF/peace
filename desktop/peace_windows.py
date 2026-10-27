"""PEACE desktop agent on Windows.

The logic is the Linux agent's (peace_desktop.py: server polling, vault copies, voice turns, voice tests, desktop
jobs); this module swaps in the Windows parts before starting it:
- notifications: Windows toasts with the same buttons (windows-toasts), registered as "PEACE";
- push-to-talk: the key combo is polled (GetAsyncKeyState), so pressing starts and releasing stops, in any order;
- microphone and speakers: PortAudio through sounddevice, devices chosen in Settings → Voz;
- links: the default browser.
Run with pythonw.exe (no console). Settings come from %LOCALAPPDATA%\\PEACE\\desktop.env, written by install.ps1.
Needs: sounddevice, numpy, windows-toasts.
"""

import ctypes
import json
import logging
import os
import re
import sys
import threading
import time
import wave
import webbrowser
from pathlib import Path

HOME_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PEACE"
RUN_DIR = HOME_DIR / "run"


def load_env(path: Path) -> None:
    """KEY=value lines (the installer's), without overriding the real environment."""
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, eq, value = line.partition("=")
            if eq and key.strip() and not key.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())


load_env(HOME_DIR / "desktop.env")
RUN_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("XDG_RUNTIME_DIR", str(RUN_DIR))  # the agent keeps its WAV files here, private to this user
sys.path.insert(0, str(Path(__file__).resolve().parent))

import peace_desktop as core  # noqa: E402

log = logging.getLogger("peace.windows")
AUMID = "PEACE.Agent"
shared_voice_test = core.run_voice_test  # kept before install() replaces it: the spoken sample is shared

# --- push-to-talk keys -----------------------------------------------------------------------------------------

# Virtual-key codes: letters and digits are their ASCII code; modifiers accept either side.
MOD_VK = {"SUPER": (0x5B, 0x5C), "SHIFT": (0x10,), "CTRL": (0x11,), "ALT": (0x12,)}
user32 = ctypes.windll.user32 if sys.platform == "win32" else None


def keys_held(combo: str) -> bool | None:
    """Are all keys of the combo down right now? None when the key state cannot be read."""
    if user32 is None:
        return None
    *mods, key = [p.strip() for p in combo.split("+")]
    groups = [MOD_VK[m] for m in mods] + [(ord(key),)]
    return all(any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in group) for group in groups)


def watch_keys(voice: "WindowsVoice") -> None:
    """Start a turn when the combo goes down. Releasing is watched by the turn itself (core.Voice.watch_release);
    after a turn the keys must come up once before the next press counts."""
    armed = True
    while True:
        held = keys_held(core.settings["ptt"])
        if held and armed and not voice.recorder and not voice.busy:
            armed = False
            voice.start()
        elif not held:
            armed = True
        time.sleep(0.03)


# --- audio -----------------------------------------------------------------------------------------------------


def _sd():
    import sounddevice

    return sounddevice


def device_id(name: str) -> str:
    """Device names become ids the server accepts (letters, digits and _.:@/-)."""
    return re.sub(r"[^A-Za-z0-9_.:@/-]+", "_", name).strip("_")[:200] or "device"


def audio_devices() -> dict:
    """Inputs and outputs of the default host API (the names the Windows sound settings show)."""
    result = {"inputs": [], "outputs": [], "error": None}
    try:
        sd = _sd()
        hostapi = sd.query_devices(kind="input")["hostapi"]
        for d in sd.query_devices():
            if d["hostapi"] != hostapi:
                continue
            entry = {"id": device_id(d["name"]), "name": d["name"]}
            if d["max_input_channels"] > 0:
                result["inputs"].append(entry)
            if d["max_output_channels"] > 0:
                result["outputs"].append(entry)
    except Exception:  # noqa: BLE001 - PortAudio raises its own errors; any failure means "no devices"
        result["error"] = "Não foi possível consultar o microfone e as colunas."
    return result


def _device(chosen: str, kind: str):
    """The sounddevice index for a Settings id; None = the Windows default."""
    if not chosen:
        return None
    sd = _sd()
    hostapi = sd.query_devices(kind=kind)["hostapi"]
    channels = "max_input_channels" if kind == "input" else "max_output_channels"
    for index, d in enumerate(sd.query_devices()):
        if d["hostapi"] == hostapi and d[channels] > 0 and device_id(d["name"]) == chosen:
            return index
    return None  # unplugged: fall back to the default rather than fail


def play(audio: Path, device: str) -> None:
    import numpy as np

    with wave.open(str(audio)) as w:
        rate, channels = w.getframerate(), w.getnchannels()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, channels)
    sd = _sd()
    sd.play(samples, rate, device=_device(device, "output"))
    sd.wait()


class Recorder:
    """16 kHz mono 16-bit capture into memory; close() writes the WAV."""

    def __init__(self, path: Path, device: str, on_level=None) -> None:
        self.path, self.frames, self.on_level, self.returncode = path, [], on_level, None
        self.stream = _sd().InputStream(samplerate=16000, channels=1, dtype="int16",
                                        device=_device(device, "input"), callback=self._chunk)
        self.stream.start()

    def _chunk(self, data, frames, when, status) -> None:
        self.frames.append(bytes(data))
        if self.on_level:
            import numpy as np

            self.on_level(min(1.0, float(np.sqrt(np.mean(np.square(data.astype(np.float32))))) / 32768))

    def close(self) -> None:
        self.stream.stop()
        self.stream.close()
        with wave.open(str(self.path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"".join(self.frames))
        self.returncode = 0


class WindowsVoice(core.Voice):
    def open_recorder(self):
        return Recorder(core.RECORDING, core.settings["input_device"])

    def close_recorder(self, recorder: Recorder) -> None:
        recorder.close()

    def monitor_capture(self, recorder: Recorder) -> None:
        core.set_state("listening")  # PortAudio delivers audio as soon as the stream starts


def run_voice_test(job: dict, voice: WindowsVoice) -> None:
    """Settings → Voz tests: 5 s of input level (and a transcription), or a spoken sample."""
    options = job["settings"]
    path = f"/api/v1/desktop/voice-test/{job['id']}"
    if job["action"] == "tts" or not core.RUNTIME:
        return shared_voice_test(job, voice)  # playback only: the shared code calls play() above
    with voice.lock:
        if voice.busy or voice.recorder:
            raise RuntimeError("A voz está ocupada; tenta novamente.")
        voice.busy = True
    audio = core.RUNTIME / "peace-test.wav"
    last = {"at": 0.0}

    def level(value: float) -> None:
        if time.monotonic() - last["at"] > 0.3:  # a few updates a second, from PortAudio's thread
            last["at"] = time.monotonic()
            threading.Thread(target=lambda: _report(path, value), daemon=True).start()

    try:
        recorder = Recorder(audio, options.get("input_device", ""), on_level=level)
        time.sleep(5)
        recorder.close()
        if not core.has_audio(audio):
            raise RuntimeError("Captação incompleta. Verifica o microfone escolhido.")
        if job["action"] == "stt":
            heard = core.transcribe(audio)
            if heard:
                core.notify_reply("Teste de transcrição (não guardado)", heard)
            message = "Transcrição recebida; confirma o texto na notificação." if heard else "Nenhuma fala reconhecida."
        else:
            message = "Teste de entrada terminado. O medidor mostra a amplitude real captada."
        core.call("POST", path, {"state": "done", "message": message, "level": 0})
    finally:
        audio.unlink(missing_ok=True)
        with voice.lock:
            core.set_state("idle")
            voice.busy = False


def _report(path: str, value: float) -> None:
    try:
        core.call("POST", path, {"state": "running", "message": "A captar durante 5 segundos…", "level": value})
    except OSError:
        pass


def capabilities() -> dict:
    d = core.voice_diagnostics
    d["stt_available"] = engine = core._up(core.VOICESTUDIO + "/health")
    d["capture_available"] = bool(d["inputs"])
    d["playback_available"] = bool(d["outputs"])
    d["ptt_bound"] = user32 is not None
    voice = engine and d["capture_available"] and d["ptt_bound"]
    speaking = core.TTS != "off" and core.settings["speak"]
    name = ("STT pt" + (f" · {core.settings['voice']}" if speaking else ", respostas escritas")) if voice else None
    return {"voice": voice, "voice_name": name, "ptt": core.settings["ptt"].replace(" ", ""),
            "llm": core._up(core.OLLAMA + "/api/tags"), "voices": core.VOICES.get(core.TTS, []) if engine else [],
            "audio": d, "actions": core.actions.availability()}


# --- notifications ---------------------------------------------------------------------------------------------

_toaster: dict = {}


def toaster():
    """Toasts under PEACE's own name: the app id is registered for this user (no admin rights needed)."""
    if "t" not in _toaster:
        import winreg

        from windows_toasts import InteractableWindowsToaster

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Classes\AppUserModelId\{AUMID}") as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "PEACE")
        _toaster["t"] = InteractableWindowsToaster("PEACE", notifierAUMID=AUMID)
    return _toaster["t"]


def handle(n: dict, action: str) -> None:
    if action == "default":
        webbrowser.open(core.DASHBOARD + n["url"])
    else:
        core.handle(n, action)


def toast(title: str, body: str, actions: list[str] = (), on_action=None, urgent: bool = False) -> None:
    from windows_toasts import Toast, ToastButton, ToastDuration

    t = Toast([title, body], duration=ToastDuration.Long if urgent else ToastDuration.Default, launch_action="default",
              actions=[ToastButton(core.LABELS[a], a) for a in actions if a in core.LABELS][:5])
    if on_action:
        t.on_activated = lambda event: threading.Thread(target=on_action, args=(event.arguments or "default",), daemon=True).start()
    toaster().show_toast(t)


def deliver(n: dict) -> bool:
    """Shown once per reminder version (receipts survive restarts), then acknowledged to the server."""
    key = f"{n['id']}:{n.get('deliver_after', n.get('created_at', ''))}:{n.get('expires_at', '')}"
    try:
        receipts = json.loads(core.RECEIPTS.read_text()) if core.RECEIPTS.exists() else {}
        if key not in receipts:

            def act(action: str) -> None:
                try:
                    handle(n, action)
                    log.info("notification %s: %s", n["id"], action)
                except OSError as e:
                    log.warning("action %s failed: %s", action, e)

            toast(n["title"], n["body"], n.get("actions") or [], act, bool(n.get("urgent")))
            receipts = {k: v for k, v in receipts.items() if v["at"] > time.time() - 7 * 86400}
            receipts[key] = {"native_id": 1, "at": time.time()}  # toasts have no numeric id; 1 = accepted
            core.RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
            temp = core.RECEIPTS.with_suffix(".tmp")
            temp.write_text(json.dumps(receipts))
            os.replace(temp, core.RECEIPTS)
        core.call("POST", f"/api/v1/notifications/{n['id']}/delivered")
        core.report_notification_delivery(notification_id=n["id"], native_id=1, result="accepted")
        return True
    except Exception as e:  # noqa: BLE001 - WinRT errors have no common base; report and retry next poll
        log.warning("notification %s not shown: %s", n["id"], type(e).__name__)
        core.report_notification_delivery(notification_id=n["id"], result="error")
        return False


def notify_reply(heard: str, reply: str) -> None:
    try:
        toast(f"«{heard}»"[:120], reply)
    except Exception as e:  # noqa: BLE001 - a reply that cannot be shown must not end the voice turn
        log.warning("reply not shown: %s", type(e).__name__)


def notification_diagnostics() -> dict:
    try:
        toaster()
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    return {"notify_send": False, "session_bus": False, "daemon_available": ok, "daemon": "Windows" if ok else None}


def install() -> None:
    """Point the shared agent at the Windows parts."""
    core.keys_held = keys_held
    core.bind_ptt = lambda combo=None, previous=None: user32 is not None  # polled: nothing to register
    core.serve_voice = watch_keys  # the Linux agent's socket for Hyprland; here, the key poller
    core.audio_devices = audio_devices
    core.play = play
    core.Voice = WindowsVoice
    core.run_voice_test = run_voice_test
    core.capabilities = capabilities
    core.deliver = deliver
    core.notify_reply = notify_reply
    core.notification_diagnostics = notification_diagnostics


if __name__ == "__main__":
    logging.basicConfig(filename=HOME_DIR / "agent.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    install()
    core.voice_diagnostics.update(audio_devices())
    core.main()

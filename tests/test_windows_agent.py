"""The Windows agent's logic on any OS: Win32, PortAudio and toasts are replaced by stand-ins."""

import importlib.util
import json
import wave
from pathlib import Path

import pytest

DESKTOP = Path(__file__).parents[1] / "desktop"


@pytest.fixture
def win(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "PEACE").mkdir()
    (tmp_path / "PEACE" / "desktop.env").write_text("PEACE_PTT_TEST=CTRL + ALT + Y\n# comment\n", encoding="utf-8")
    monkeypatch.delenv("PEACE_PTT_TEST", raising=False)
    monkeypatch.syspath_prepend(str(DESKTOP))
    spec = importlib.util.spec_from_file_location("peace_windows_under_test", DESKTOP / "peace_windows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.core, "RECEIPTS", tmp_path / "receipts.json")
    return module


class Keys:
    def __init__(self, down=()):
        self.down = set(down)

    def GetAsyncKeyState(self, vk):  # noqa: N802 - the Win32 name
        return 0x8000 if vk in self.down else 0


def test_settings_file_is_read_without_overriding_the_environment(win, monkeypatch):
    import os

    assert os.environ["PEACE_PTT_TEST"] == "CTRL + ALT + Y"
    monkeypatch.setenv("PEACE_PTT_TEST", "kept")
    win.load_env(win.HOME_DIR / "desktop.env")
    assert os.environ["PEACE_PTT_TEST"] == "kept"


def test_push_to_talk_keys_any_side_and_any_release_order(win, monkeypatch):
    monkeypatch.setattr(win, "user32", Keys({0x5C, ord("Y")}))  # right Windows key + Y
    assert win.keys_held("SUPER + Y") is True
    monkeypatch.setattr(win, "user32", Keys({ord("Y")}))  # the Windows key went up first
    assert win.keys_held("SUPER + Y") is False
    monkeypatch.setattr(win, "user32", Keys({0x11, 0x12, ord("7")}))
    assert win.keys_held("CTRL + ALT + 7") is True
    monkeypatch.setattr(win, "user32", None)
    assert win.keys_held("SUPER + Y") is None  # not Windows: the turn's own time limit applies


def test_device_names_become_ids_the_server_accepts(win):
    import re

    pattern = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9_.:@/-]{0,199})?$")  # the server's DEVICE_RE
    for name in ("Microfone (Realtek(R) Audio)", "Auscultadores (WH-1000XM4 Stereo)", "!!!", "x" * 400):
        assert pattern.fullmatch(win.device_id(name)), name


def test_recording_writes_a_16_khz_wav(win, monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")

    class Stream:
        def __init__(self, samplerate, channels, dtype, device, callback):
            assert (samplerate, channels, dtype, device) == (16000, 1, "int16", None)
            self.callback = callback

        def start(self):
            self.callback(np.full((1600, 1), 1000, dtype=np.int16), 1600, None, None)

        def stop(self):
            pass

        def close(self):
            pass

    levels = []
    monkeypatch.setattr(win, "_sd", lambda: type("SD", (), {"InputStream": Stream})())
    rec = win.Recorder(tmp_path / "a.wav", "", on_level=levels.append)
    rec.close()
    with wave.open(str(tmp_path / "a.wav")) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (16000, 1, 1600)
    assert levels and 0.02 < levels[0] < 0.04 and win.core.has_audio(tmp_path / "a.wav")


def test_a_reminder_is_shown_once_and_its_buttons_act(win, monkeypatch):
    shown, calls, handled = [], [], []
    monkeypatch.setattr(win, "toast", lambda title, body, actions, on_action, urgent: shown.append((title, actions, on_action)))
    monkeypatch.setattr(win.core, "call", lambda method, path, body=None: calls.append(path))
    monkeypatch.setattr(win.core, "handle", lambda n, action: handled.append(action))
    n = {"id": 7, "title": "Avaliação amanhã", "body": "BD às 16:00", "actions": ["done", "snooze"], "urgent": True,
         "url": "/prazos", "created_at": "t", "expires_at": "e"}
    assert win.deliver(n) and win.deliver(n)
    assert len(shown) == 1 and shown[0][1] == ["done", "snooze"]  # a restart or a second poll never repeats it
    assert calls.count("/api/v1/notifications/7/delivered") == 2
    shown[0][2]("snooze")
    opened = []
    monkeypatch.setattr(win.webbrowser, "open", opened.append)
    shown[0][2]("default")
    assert handled == ["snooze"] and opened == [win.core.DASHBOARD + "/prazos"]
    assert json.loads(win.core.RECEIPTS.read_text())


def test_install_points_the_shared_agent_at_the_windows_parts(win, monkeypatch):
    for name in ("keys_held", "bind_ptt", "serve_voice", "audio_devices", "play", "Voice", "run_voice_test",
                 "capabilities", "deliver", "notify_reply", "notification_diagnostics"):
        monkeypatch.setattr(win.core, name, getattr(win.core, name))  # restored after the test
    win.install()
    assert win.core.Voice is win.WindowsVoice and win.core.serve_voice is win.watch_keys and win.core.play is win.play
    assert win.shared_voice_test is not win.core.run_voice_test  # the spoken sample is not the Windows test itself


# --- desktop actions on Windows ---------------------------------------------------------------------------------

class FakeWin32:
    def __init__(self):
        self.windows = {"0x10": {"address": "0x10", "class": "obsidian", "initialClass": "obsidian", "title": "Notas - Obsidian",
                                 "workspace": {"id": 1}, "focusHistoryID": 1},
                        "0x20": {"address": "0x20", "class": "brave", "initialClass": "brave", "title": "Moodle",
                                 "workspace": {"id": 1}, "focusHistoryID": 0}}
        self.opened, self.trashed, self.rects = [], [], {}

    def clients(self):
        return [dict(w) for w in self.windows.values()]

    def focus(self, address):
        for w in self.windows.values():
            w["focusHistoryID"] = 0 if w["address"] == address else 1
        return True

    def close(self, address):
        self.windows.pop(address)

    def exists(self, address):
        return address in self.windows

    def place(self, address, x, y, width, height):
        self.rects[address] = (x * 19, y * 10, width * 19, height * 10)
        return self.rects[address]

    def rect(self, address):
        return self.rects[address]

    def shell_open(self, target, params=""):
        self.opened.append((target, params))
        return True

    def trash(self, path):
        self.trashed.append(path)
        return True

    def applications(self):
        return {"Obsidian": {"id": "Obsidian", "name": "Obsidian", "path": r"C:\Start\Obsidian.lnk", "wm_class": "", "hidden": False}}


@pytest.fixture
def actions(monkeypatch):
    spec = importlib.util.spec_from_file_location("peace_actions_on_windows", DESKTOP / "peace_actions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.fake = FakeWin32()
    monkeypatch.setattr(module, "WINDOWS", True)
    monkeypatch.setattr(module, "win32", module.fake, raising=False)
    monkeypatch.setattr(module, "audit", lambda entry: None)
    monkeypatch.setattr(module, "OPEN_WAIT", 0.1)  # the stand-in never opens a new window
    monkeypatch.setattr(module, "LAUNCH_WAIT", 0.1)
    return module


def test_window_tools_use_win32(actions):
    assert actions.run("windows.focus", {"window": "obsidian"})["verified"]
    assert actions.run("windows.place", {"window": "obsidian", "x": 0, "y": 0, "width": 50, "height": 100})["verified"]
    assert not actions.run("windows.close", {"window": "brave"})["ok"]  # can lose work: needs a «sim»
    assert actions.run("windows.close", {"window": "brave"}, confirmed=True)["verified"] and "0x20" not in actions.fake.windows
    result = actions.run("workspaces.switch", {"workspace": 2})
    assert (result["ok"], result["code"]) == (False, "unsupported") and "Windows" in result["message"]


def test_files_and_apps_open_through_the_shell(actions, monkeypatch, tmp_path):
    (tmp_path / "Resumo BD.pdf").write_text("x")
    monkeypatch.setenv("PEACE_FILE_ROOTS", f"documentos={tmp_path}")
    assert actions.run("files.open", {"path": "Resumo BD.pdf", "root": "documentos"})["ok"]
    pdf = (tmp_path / "Resumo BD.pdf").resolve()  # Windows runners hand out 8.3 short temp paths
    assert actions.fake.opened[-1] == (str(pdf), "")
    actions.run("apps.launch", {"app": "obsidian", "files": ["Resumo BD.pdf"], "root": "documentos"})
    assert actions.fake.opened[-1] == (r"C:\Start\Obsidian.lnk", f'"{pdf}"')
    assert actions.run("files.trash", {"path": "Resumo BD.pdf", "root": "documentos"}, confirmed=True)["ok"]
    assert actions.fake.trashed == [pdf]
    assert not actions.run("files.mkdir", {"path": "a:b", "root": "documentos"})["ok"]  # not a valid Windows name


def test_installer_survives_being_read_as_windows_1252():
    """PowerShell 5.1 reads a downloaded .ps1 without BOM as Windows-1252; UTF-8 «→» or «—» would then contain
    bytes it takes for quote marks and break the script."""
    text = (DESKTOP.parent / "install.ps1").read_bytes().decode("cp1252", errors="replace")
    assert not set("‘’“”") & set(text)

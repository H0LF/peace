"""The desktop agent is a standalone stdlib script; check its notify-send command and action routing."""

import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("peace_desktop", Path(__file__).parents[1] / "desktop" / "peace_desktop.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
REAL_PTT_CONFLICT = agent.ptt_conflict  # the autouse fixture stubs it


@pytest.fixture(autouse=True)
def private_voice_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "RUNTIME", tmp_path)
    monkeypatch.setattr(agent, "RECORDING", tmp_path / "voice.wav")
    monkeypatch.setattr(agent, "settings", dict(agent.settings))
    monkeypatch.setattr(agent, "ptt_conflict", lambda combo: False)
    # Never reach the real Hyprland or Brave from these tests.
    monkeypatch.setattr(agent.actions, "availability", lambda: {"categories": [], "hyprland": False, "browser": False})

NOTE = {"id": 7, "kind": "deadline", "title": "Entrega em menos de 2 horas", "body": "Q&A <b>sockets</b> · hoje às 18:00",
        "url": "/prazos", "actions": ["open", "done", "snooze"], "urgent": True, "task_id": 3, "session_id": None}


def test_command_is_silent_escaped_and_has_actions():
    cmd = agent.command(NOTE)
    assert "--hint=boolean:suppress-sound:true" in cmd and "--urgency=critical" in cmd and "--wait" in cmd
    assert ["--action=default=Abrir", "--action=done=Concluída", "--action=snooze=Adiar"] == [a for a in cmd if a.startswith("--action")]
    assert cmd[-3:] == ["--", "Entrega em menos de 2 horas", "Q&amp;A &lt;b&gt;sockets&lt;/b&gt; · hoje às 18:00"]


def test_notify_send_cannot_unescape_scraped_markup():
    assert agent.command({**NOTE, "body": r"\074b\076texto\074/b\076"})[-1] == r"\\074b\\076texto\\074/b\\076"


def test_actions_map_to_api_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "call", lambda *a: calls.append(a))
    agent.handle(NOTE, "done")
    agent.handle(NOTE, "snooze")
    agent.handle({**NOTE, "kind": "session", "session_id": 9}, "skip")
    assert calls == [
        ("PATCH", "/api/v1/tasks/3", {"status": "COMPLETED"}),
        ("POST", "/api/v1/notifications/7/snooze", {"minutes": 60}),
        ("PATCH", "/api/v1/sessions/9", {"status": "skipped"}),
    ]


def test_delivery_requires_a_real_native_id_and_retries_ack_without_duplicate(monkeypatch, tmp_path):
    import os
    from unittest.mock import Mock
    monkeypatch.setattr(agent, "RECEIPTS", tmp_path / "notifications.json")
    calls, launches = [], []

    def launch(args, **kwargs):
        launches.append(args)
        os.write(kwargs["pass_fds"][0], b"42\n")
        return Mock(communicate=lambda **kw: ("", ""))

    def call(method, path, body=None):
        calls.append(path)
        if path.endswith("/delivered") and calls.count(path) == 1:
            raise OSError("temporary API failure")

    monkeypatch.setattr(agent.subprocess, "Popen", launch)
    monkeypatch.setattr(agent, "call", call)
    note = {**NOTE, "deliver_after": "2026-10-26T13:00:00Z"}
    assert agent.deliver(note) is False
    assert agent.deliver(note) is True
    assert len(launches) == 1
    # A snooze is a deliberate new delivery, unlike a failed HTTP acknowledgement.
    assert agent.deliver({**note, "deliver_after": "2026-10-26T14:00:00Z"}) is True
    assert len(launches) == 2
    # A changed deadline re-arms the same server row with a new expiry.
    assert agent.deliver({**note, "expires_at": "2026-10-27T17:00:00Z"}) is True
    assert len(launches) == 3


def test_no_native_id_means_no_delivery_ack(monkeypatch, tmp_path):
    from unittest.mock import Mock
    monkeypatch.setattr(agent, "RECEIPTS", tmp_path / "notifications.json")
    monkeypatch.setattr(agent, "NATIVE_ACK_TIMEOUT", 0.01)
    calls = []
    monkeypatch.setattr(agent, "call", lambda *a: calls.append(a))
    monkeypatch.setattr(agent.subprocess, "Popen", lambda *a, **kw: Mock(communicate=lambda **k: ("", "no bus")))
    assert agent.deliver({**NOTE, "deliver_after": "later"}) is False
    assert not [c for c in calls if c[1].endswith("/delivered")]


def test_notification_diagnostics_do_not_expose_session_addresses(monkeypatch):
    from unittest.mock import Mock
    monkeypatch.setattr(agent.shutil, "which", lambda executable: "/usr/bin/" + executable)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/private/address")
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **kw: Mock(returncode=0, stdout='ssss "Brain Shell" "vendor" "1" "1.2"'))
    status = agent.notification_diagnostics()
    assert status["daemon_available"] and status["notify_send"] and status["session_bus"]
    assert "/private/address" not in str(status)


# --- push-to-talk ----------------------------------------------------------------------------------


def test_multipart_upload_is_well_formed():
    body, kind = agent.multipart({"language": "pt"}, "file", "fala.wav", b"RIFF....")
    boundary = kind.split("boundary=")[1]
    assert body.startswith(f"--{boundary}\r\n".encode()) and body.endswith(f"--{boundary}--\r\n".encode())
    assert b'name="language"\r\n\r\npt\r\n' in body and b'filename="fala.wav"\r\nContent-Type: audio/wav\r\n\r\nRIFF....' in body


def test_capabilities_report_only_what_answers(monkeypatch):
    monkeypatch.setitem(agent.voice_diagnostics, 'inputs', [{'id': 'mic', 'name': 'Mic'}])
    monkeypatch.setitem(agent.voice_diagnostics, 'ptt_bound', True)
    monkeypatch.setattr(agent.shutil, 'which', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr(agent, "_up", lambda url: "3900" in url)
    assert agent.capabilities() == {"voice": True, "voice_name": "STT pt, respostas escritas", "ptt": "SUPER+Y", "llm": False, "voices": [], "audio": agent.voice_diagnostics,
                                   "actions": {"categories": [], "hyprland": False, "browser": False}}
    monkeypatch.setattr(agent, "_up", lambda url: False)
    assert agent.capabilities()["voice"] is False and agent.capabilities()["voice_name"] is None


def test_a_voice_turn_transcribes_asks_and_answers(monkeypatch, tmp_path):
    states, shown = [], []
    monkeypatch.setattr(agent, "set_state", states.append)
    monkeypatch.setattr(agent, "transcribe", lambda wav: "o que tenho amanhã")
    monkeypatch.setattr(agent, "call", lambda method, path, body=None: {"reply": "Amanhã tens 2 aulas."} if path.endswith("/ask") else None)
    monkeypatch.setattr(agent, "notify_reply", lambda heard, reply: shown.append((heard, reply)))
    agent.Voice().answer()
    assert states == ["thinking", "idle"] and shown == [("o que tenho amanhã", "Amanhã tens 2 aulas.")]
    monkeypatch.setattr(agent, "transcribe", lambda wav: "")
    agent.Voice().answer()
    assert shown[-1][1].startswith("Não percebi.") and states[-1] == "idle"


def test_a_tap_is_not_a_question(monkeypatch):
    states = []
    monkeypatch.setattr(agent, "set_state", states.append)

    class Recorder:
        def send_signal(self, sig):
            pass

        def wait(self, timeout):
            pass

    voice = agent.Voice()
    voice.recorder, voice.started = Recorder(), agent.time.monotonic()
    started = []
    monkeypatch.setattr(agent.threading, "Thread", lambda **kw: started.append(kw) or type("T", (), {"start": lambda self: None})())
    voice.stop()
    assert states == ["idle"] and not started


def test_ptt_binding_uses_hyprctl_at_runtime(monkeypatch):
    runs = []
    monkeypatch.setattr(agent.subprocess, "run", lambda args, **kw: runs.append(args) or type("R", (), {"stdout": "ok"})())
    assert agent.bind_ptt()
    assert [r[:2] for r in runs] == [["hyprctl", "eval"]] * 3
    assert runs[0][2] == 'hl.unbind("SUPER + Y")'
    assert runs[1][2].startswith('hl.bind("SUPER + Y", hl.dsp.exec_cmd("') and runs[1][2].endswith('ptt start"), { description = "PEACE push-to-talk" })')
    assert runs[2][2].endswith('ptt stop"), { release = true, description = "PEACE push-to-talk" })')


def test_only_peace_tagged_lua_binds_may_be_replaced(monkeypatch):
    binds = [{"modmask": 64, "key": "Y", "dispatcher": "__lua", "arg": "237", "description": "PEACE push-to-talk"}]
    monkeypatch.setattr(agent.subprocess, "run", lambda args, **kw: type("R", (), {"stdout": json.dumps(binds)})())
    assert REAL_PTT_CONFLICT("SUPER + Y") is False
    binds[0]["description"] = ""  # someone else's Lua bind: never replaced
    assert REAL_PTT_CONFLICT("SUPER + Y") is True


def test_ptt_binding_falls_back_to_legacy_configs(monkeypatch):
    runs = []

    def run(args, **kw):
        runs.append(args)
        return type("R", (), {"stdout": "ok" if args[1] == "keyword" else "eval needs a Lua config"})()

    monkeypatch.setattr(agent.subprocess, "run", run)
    assert agent.bind_ptt()
    assert [r[1:3] for r in runs if r[1] == "keyword"] == [["keyword", "unbind"], ["keyword", "bind"], ["keyword", "bindr"]]
    assert runs[-2][3].startswith("SUPER,Y,exec,")


def test_times_are_read_as_spoken_portuguese():
    assert agent.for_speech("Amanhã às 17:00 e às 19:40, e às 1:00.") == "Amanhã às 17 horas e às 19 e 40, e às 1 hora."
    assert agent.for_speech("Planeei 2 sessões, 1 h 30 min no total. «Meta3»") == "Planeei 2 sessões, 1 hora e 30 minutos no total. Meta3"


def test_voice_settings_from_the_dashboard_are_validated_and_applied(monkeypatch):
    runs = []
    monkeypatch.setattr(agent.subprocess, "run", lambda args, **kw: runs.append(args) or type("R", (), {"stdout": "ok"})())
    monkeypatch.setitem(agent.settings, "ptt", "SUPER + Y")
    monkeypatch.setattr(agent, "TTS", "supertonic3")
    agent.apply_voice_settings({"ptt": 'SUPER + Y")) os.execute("x', "speak": False, "voice": "F3"})
    assert runs == [] and agent.settings["ptt"] == "SUPER + Y"  # never reaches hyprctl
    assert agent.settings["speak"] is False and agent.settings["voice"] == "F3"
    agent.apply_voice_settings({"ptt": "SUPER + SHIFT + D", "speak": True, "voice": "bad voice!"})
    assert agent.settings["ptt"] == "SUPER + SHIFT + D" and agent.settings["voice"] == "F3"
    agent.apply_voice_settings({"voice": "M1", "language": "pt"})  # female voices only
    assert agent.settings["voice"] == "F3" and agent.settings["language"] == "pt"
    agent.apply_voice_settings({"language": "fr"})
    assert agent.settings["language"] == "pt"
    monkeypatch.setitem(agent.settings, "language", "en")
    assert {r[2] for r in runs if r[2].startswith("hl.unbind")} == {'hl.unbind("SUPER + Y")', 'hl.unbind("SUPER + SHIFT + D")'}
    monkeypatch.setitem(agent.settings, "ptt", "SUPER + Y")


def test_a_press_while_answering_is_ignored_and_failures_are_reported(monkeypatch):
    started, shown = [], []
    monkeypatch.setattr(agent.subprocess, "Popen", lambda *a, **k: started.append(a) or object())
    voice = agent.Voice()
    voice.busy = True
    voice.start()
    assert started == []  # the previous turn is still being answered
    monkeypatch.setattr(agent, "set_state", lambda s: None)
    monkeypatch.setattr(agent, "transcribe", lambda wav: {"not": "text"})  # anything unexpected
    monkeypatch.setattr(agent, "notify_reply", lambda heard, reply: shown.append(reply))
    voice.answer()
    assert shown and shown[0].startswith("A voz não está disponível") and voice.busy is False


def test_a_combo_that_fails_to_bind_is_not_retried_every_poll(monkeypatch):
    runs = []
    monkeypatch.setattr(agent.subprocess, "run", lambda args, **kw: runs.append(args) or type("R", (), {"stdout": "error"})())
    monkeypatch.setitem(agent.settings, "ptt", "SUPER + Y")
    agent.apply_voice_settings({"ptt": "SUPER + K"})
    first = len(runs)
    agent.apply_voice_settings({"ptt": "SUPER + K"})
    assert len(runs) == first and agent.settings["ptt"] == "SUPER + Y"
    agent.settings.pop("failed", None)


def test_audio_devices_exclude_monitors_and_report_errors(monkeypatch):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(agent.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps([
        {'name': 'mic.one', 'description': 'USB microphone'}, {'name': 'sink.monitor', 'description': 'Monitor'}])))
    assert agent.audio_devices()['inputs'] == [{'id': 'mic.one', 'name': 'USB microphone'}]
    monkeypatch.setattr(agent.subprocess, 'run', lambda *a, **kw: (_ for _ in ()).throw(OSError('unavailable')))
    assert agent.audio_devices()['error']


def test_selected_devices_are_validated_and_used(monkeypatch):
    monkeypatch.setitem(agent.settings, 'input_device', '')
    monkeypatch.setitem(agent.settings, 'output_device', '')
    agent.apply_voice_settings({'input_device': 'alsa_input.usb-1', 'output_device': 'alsa_output.1'})
    assert agent.record_command(Path('/test.wav'))[-3:] == ['--target', 'alsa_input.usb-1', '/test.wav']
    agent.apply_voice_settings({'input_device': '--bad', 'output_device': 'invalid\nname'})
    assert agent.settings['input_device'] == 'alsa_input.usb-1'
    assert agent.settings['output_device'] == 'alsa_output.1'


def test_voice_audio_is_removed_after_failure(monkeypatch, tmp_path):
    recording = tmp_path / 'voice.wav'
    recording.write_bytes(b'private audio')
    monkeypatch.setattr(agent, 'RECORDING', recording)
    monkeypatch.setattr(agent, 'set_state', lambda s: None)
    monkeypatch.setattr(agent, 'transcribe', lambda p: (_ for _ in ()).throw(OSError('offline')))
    monkeypatch.setattr(agent, 'notify_reply', lambda *a: None)
    agent.Voice().answer()
    assert not recording.exists()


def test_tts_uses_selected_output_and_removes_audio(monkeypatch, tmp_path):
    import io
    runs, states = [], []
    monkeypatch.setattr(agent.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(b'RIFF.test'))
    monkeypatch.setattr(agent.subprocess, 'run', lambda args, **kw: runs.append((args, kw)))
    monkeypatch.setattr(agent, 'set_state', states.append)
    monkeypatch.setitem(agent.settings, 'output_device', 'alsa_output.usb')
    agent.speak('Olá')
    assert runs[0][0] == ['pw-play', '--target', 'alsa_output.usb', str(tmp_path / 'peace-reply.wav')]
    assert runs[0][1]['check'] is True and states == ['speaking']
    assert not (tmp_path / 'peace-reply.wav').exists()


def test_microphone_diagnostic_uses_real_samples_without_agent_command(monkeypatch):
    import io
    from types import SimpleNamespace
    chunks = iter([b'\x00\x40' * 80000, b''])
    reported = agent.threading.Event()
    proc = SimpleNamespace(stdout=io.BytesIO(), poll=lambda: 0, returncode=0, wait=lambda **kw: None)
    calls = []
    monkeypatch.setattr(agent.subprocess, 'Popen', lambda *a, **kw: proc)
    monkeypatch.setattr(agent.select, 'select', lambda *a: ([proc.stdout], [], []))
    def read(*args):
        chunk = next(chunks)
        if not chunk:
            assert reported.wait(timeout=1)
        return chunk
    monkeypatch.setattr(agent.os, 'read', read)
    monkeypatch.setattr(proc.stdout, 'fileno', lambda: 1)
    monkeypatch.setattr(agent, 'call', lambda method, path, body=None: calls.append((path, body)) or reported.set())
    monkeypatch.setattr(agent, 'set_state', lambda s: None)
    voice = agent.Voice()
    agent.run_voice_test({'id': 'one', 'action': 'meter', 'settings': {}}, voice)
    assert calls[0][1]['level'] == 0.5
    assert calls[-1][1]['state'] == 'done' and not voice.busy
    assert all('/assistant/ask' not in path for path, _ in calls)


def test_voice_diagnostic_never_interrupts_a_held_shortcut(monkeypatch):
    voice = agent.Voice()
    voice.busy = True
    with pytest.raises(RuntimeError):
        agent.run_voice_test({'id': 'one', 'action': 'meter', 'settings': {}}, voice)
    assert voice.busy


def test_capture_start_failure_is_reported_and_old_audio_is_removed(monkeypatch):
    agent.RECORDING.write_bytes(b'old private audio')
    shown = []
    monkeypatch.setattr(agent.subprocess, 'Popen', lambda *a, **kw: (_ for _ in ()).throw(OSError('no pw-record')))
    monkeypatch.setattr(agent, 'notify_reply', lambda *a: shown.append(a))
    voice = agent.Voice()
    voice.start()
    assert not agent.RECORDING.exists() and voice.recorder is None and not voice.busy
    assert shown


def test_unsaved_diagnostic_does_not_rebind_or_change_live_settings(monkeypatch):
    before = dict(agent.settings)
    spoken = []
    monkeypatch.setattr(agent, 'TTS', 'supertonic3')
    monkeypatch.setattr(agent, 'speak', lambda text, **kw: spoken.append(kw))
    monkeypatch.setattr(agent, 'call', lambda *a: None)
    monkeypatch.setattr(agent, 'set_state', lambda s: None)
    monkeypatch.setattr(agent, 'bind_ptt', lambda *a, **kw: pytest.fail('diagnostic must not rebind shortcuts'))
    agent.run_voice_test({'id': 'one', 'action': 'tts', 'settings': {'ptt': 'SUPER + K', 'voice': 'F3', 'output_device': 'usb.output'}}, agent.Voice())
    assert agent.settings == before
    assert spoken == [{'voice_name': 'F3', 'output_device': 'usb.output', 'language': 'en'}]


def test_release_handles_exited_capture_and_does_not_transcribe_failure(monkeypatch):
    from types import SimpleNamespace
    agent.RECORDING.write_bytes(b'RIFF' * 30)
    proc = SimpleNamespace(send_signal=lambda sig: (_ for _ in ()).throw(ProcessLookupError()), wait=lambda **kw: None, returncode=1)
    voice = agent.Voice()
    voice.recorder, voice.started = proc, agent.time.monotonic() - 1
    states, replies = [], []
    monkeypatch.setattr(agent, 'set_state', states.append)
    monkeypatch.setattr(agent, 'notify_reply', lambda *a: replies.append(a))
    voice.stop()
    assert not agent.RECORDING.exists() and not voice.busy and states == ['idle'] and replies


def test_voice_readiness_requires_capture_and_bound_shortcut(monkeypatch):
    monkeypatch.setattr(agent, '_up', lambda url: True)
    monkeypatch.setitem(agent.voice_diagnostics, 'inputs', [])
    monkeypatch.setitem(agent.voice_diagnostics, 'ptt_bound', False)
    status = agent.capabilities()
    assert status['voice'] is False and status['audio']['stt_available'] is True
    assert status['audio']['capture_available'] is False


def test_expired_test_consent_never_opens_microphone(monkeypatch):
    monkeypatch.setattr(agent.subprocess, 'Popen', lambda *a, **kw: pytest.fail('expired consent must not capture'))
    with pytest.raises(RuntimeError):
        agent.run_voice_test({'id': 'old', 'action': 'meter', 'settings': {}, 'capture_before': '2000-01-01T00:00:00+00:00'}, agent.Voice())


def test_pending_voice_states_are_bounded_and_keep_only_latest(monkeypatch):
    import queue
    monkeypatch.setattr(agent, 'state_updates', queue.Queue(maxsize=1))
    monkeypatch.setattr(agent, 'state_worker', object())  # an existing network writer is in flight
    agent.set_state('listening')
    agent.set_state('thinking')
    agent.set_state('idle')
    assert agent.state_updates.qsize() == 1
    assert agent.state_updates.get_nowait() == 'idle'


def test_a_desktop_job_reports_each_step_and_its_final_result(monkeypatch):
    posts = []
    monkeypatch.setattr(agent.actions, "run", lambda name, args, confirmed=False, job="": {"tool": name, "ok": name != "bad", "verified": True,
                                                                                           "code": None, "message": name, "data": {}})
    monkeypatch.setattr(agent, "call", lambda method, path, body=None: (posts.append((path, json.loads(json.dumps(body)))), {"cancel": False})[1])
    agent.run_job({"id": "j1", "steps": [{"tool": "a", "args": {}}, {"tool": "bad", "args": {}}], "confirmed": False})
    assert [[r["ok"] for r in b["results"]] for _, b in posts] == [[True], [True, False], [True, False]]
    assert posts[-1][1] | {"results": None} == {"results": None, "final": True, "cancelled": False}
    assert all(p == "/api/v1/desktop/jobs/j1/progress" for p, _ in posts)


def test_a_cancelled_desktop_job_stops_before_the_next_step(monkeypatch):
    ran, posts = [], []
    monkeypatch.setattr(agent.actions, "run", lambda name, args, confirmed=False, job="": ran.append(name) or {"tool": name, "ok": True,
                        "verified": True, "code": None, "message": "", "data": {}})
    monkeypatch.setattr(agent, "call", lambda method, path, body=None: (posts.append(body), {"cancel": True})[1])
    agent.run_job({"id": "j2", "steps": [{"tool": "a"}, {"tool": "b"}], "confirmed": False})
    assert ran == ["a"] and posts[-1]["cancelled"] is True and posts[-1]["final"] is False


def test_a_job_the_server_gave_up_on_is_not_retried(monkeypatch):
    import urllib.error
    attempts = []
    monkeypatch.setattr(agent.actions, "run_plan", lambda *a, **kw: ([], False))

    def call(method, path, body=None):
        attempts.append(path)
        raise urllib.error.HTTPError(path, 409, "gone", None, None)
    monkeypatch.setattr(agent, "call", call)
    agent.run_job({"id": "j3", "steps": []})
    assert len(attempts) == 1


def test_a_voice_request_for_the_desktop_speaks_what_was_verified(monkeypatch):
    shown, polls = [], []
    monkeypatch.setattr(agent, "set_state", lambda s: None)
    monkeypatch.setattr(agent, "transcribe", lambda wav: "vamos estudar sistemas operativos")
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)

    def call(method, path, body=None):
        if path.endswith("/ask"):
            return {"reply": "A tratar disso no PC: preparar o estudo.", "job": {"id": "j4", "state": "pending"}}
        polls.append(path)
        return {"state": "running" if len(polls) < 3 else "partial", "summary": "Fiz 2 de 3 passos. Falhou: A extensão PEACE do Brave não está ligada."}
    monkeypatch.setattr(agent, "call", call)
    monkeypatch.setattr(agent, "notify_reply", lambda heard, reply: shown.append(reply))
    agent.Voice().answer()
    assert polls == ["/api/v1/desktop/jobs/j4"] * 3 and shown == ["Fiz 2 de 3 passos. Falhou: A extensão PEACE do Brave não está ligada."]


def test_the_heartbeat_reports_desktop_action_availability(monkeypatch):
    monkeypatch.setattr(agent, "_up", lambda url: False)
    monkeypatch.setattr(agent.actions, "availability", lambda: {"categories": ["files"], "hyprland": True, "browser": False})
    assert agent.capabilities()["actions"] == {"categories": ["files"], "hyprland": True, "browser": False}


def test_a_complete_recording_is_answered_even_if_pw_record_exits_1(monkeypatch):
    """pw-record exits 1 after SIGINT on this desktop although the WAV is complete (seen live)."""
    import wave
    from types import SimpleNamespace
    with wave.open(str(agent.RECORDING), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(b"\0\1" * 16000)
    proc = SimpleNamespace(send_signal=lambda sig: None, wait=lambda **kw: None, returncode=1)
    voice = agent.Voice()
    voice.recorder, voice.started = proc, agent.time.monotonic() - 1
    answered = []
    monkeypatch.setattr(agent.threading, "Thread", lambda target, daemon: SimpleNamespace(start=lambda: answered.append(target)))
    voice.stop()
    assert answered == [voice.answer]


def test_spoken_replies_are_translated_to_english_or_fall_back_to_portuguese(monkeypatch, tmp_path):
    sent = []

    class Reply:
        def __init__(self, data): self.data = data
        def read(self): return self.data
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(request, timeout):
        if request.full_url.endswith("/api/chat"):
            if translate_fails:
                raise agent.urllib.error.URLError("model down")
            return Reply(b'{"message": {"content": "You have an exam at 5 pm."}}')
        sent.append(agent.json.loads(request.data))
        return Reply(b"RIFF")

    monkeypatch.setattr(agent.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(agent, "set_state", lambda s: None)
    monkeypatch.setattr(agent, "RUNTIME", tmp_path)
    monkeypatch.setitem(agent.settings, "language", "en")
    translate_fails = False
    agent.speak("Tens um exame às 17:00.")
    translate_fails = True
    agent.speak("Tens um exame às 17:00.")
    assert [(b["input"], b["language"]) for b in sent] == [("You have an exam at 5 pm.", "en"), ("Tens um exame às 17 horas.", "pt")]


def test_push_to_talk_release_is_read_from_the_keyboards(monkeypatch, tmp_path):
    keyboard = tmp_path / "usb-kbd-event-kbd"
    keyboard.write_bytes(b"")
    down = set()

    def ioctl(f, request, buf):
        for code in down:
            buf[code // 8] |= 1 << (code % 8)

    monkeypatch.setattr(agent.Path, "glob", lambda self, pattern: [keyboard] if str(self) == "/dev/input/by-id" else [])
    monkeypatch.setattr(agent.fcntl, "ioctl", ioctl)
    down.update({125, 21})  # left SUPER + Y
    assert agent.keys_held("SUPER + Y") is True
    down.discard(125)  # SUPER let go first: Hyprland would miss this release
    assert agent.keys_held("SUPER + Y") is False
    monkeypatch.setattr(agent.Path, "glob", lambda self, pattern: [])
    assert agent.keys_held("SUPER + Y") is None  # no readable keyboard: fall back to Hyprland and the time limit

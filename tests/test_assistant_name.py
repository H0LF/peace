from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from peace import assistant, speech
from peace.assistant import addressed, named, study_blocks


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    # The rules answer. The real check would ask this PC's Ollama and cache the answer for the tests that follow.
    monkeypatch.setattr(assistant, "llm_reachable", lambda *a: False)


@pytest.mark.parametrize("said, heard", [
    ("Jarvis, o que tenho amanhã?", "o que tenho amanhã?"),
    ("jarvis o que tenho amanhã?", "o que tenho amanhã?"),
    ("Olá Jarvis, o que tenho hoje?", "o que tenho hoje?"),
    ("hey Jarvis", "hey"),
    ("Jarvis", "olá"),
    ("o que tenho amanhã, Jarvis?", "o que tenho amanhã"),
    ("obrigado Jarvis", "obrigado"),
    ("PEACE, o que tenho hoje?", "o que tenho hoje?"),  # the old name keeps working
    ("marca reunião com o Jarvis", "marca reunião com o Jarvis"),  # not a form of address
    ("Jarvisson disse olá", "Jarvisson disse olá"),
])
def test_the_name_is_only_removed_as_a_form_of_address(said, heard):
    assert addressed(said, "Jarvis") == heard


def test_a_name_with_regex_characters_is_taken_literally():
    assert addressed("J.A, olá", "J.A") == "olá"
    assert addressed("JxA, olá", "J.A") == "JxA, olá"


def test_replies_use_the_chosen_name_and_read_properly():
    assert named("A PEACE precisa de ti", "Jarvis") == "Jarvis precisa de ti"
    assert named("o painel da PEACE", "Jarvis") == "o painel de Jarvis"
    assert named("PEACE_TOKEN e PEACE", "Jarvis") == "PEACE_TOKEN e Jarvis"
    assert named("A PEACE precisa", "PEACE") == "A PEACE precisa"


def test_the_name_is_saved_validated_and_answers_requests(client):
    assert client.get("/api/v1/preferences/appearance").json()["assistant_name"] == "PEACE"
    base = {"accent": "#6fd3ff", "name": "Ana"}
    for bad in ("", "   ", "{evil}", "a" * 31, "<b>x</b>"):
        assert client.put("/api/v1/preferences/appearance", json=base | {"assistant_name": bad}).status_code == 422
    assert client.put("/api/v1/preferences/appearance", json=base | {"assistant_name": " Jarvis "}).json()["assistant_name"] == "Jarvis"
    reply = client.post("/api/v1/assistant/ask", json={"text": "Jarvis, o que tenho amanhã?", "source": "text"}).json()
    assert reply["intent"] == "day"
    said = client.get("/api/v1/assistant/messages?limit=2").json()
    assert any("Jarvis, o que tenho amanhã?" in m.values() for m in said)  # the history keeps what was really said


def _session(title, start, minutes, event_id=1):
    return SimpleNamespace(task=None, event=SimpleNamespace(title=title), event_id=event_id, task_id=None, course_id=1,
                           starts_at=start, ends_at=start + timedelta(minutes=minutes))


def test_sessions_with_only_a_break_between_them_are_one_block():
    at = datetime(2026, 11, 4, 9, tzinfo=UTC)
    blocks = study_blocks([
        _session("AM1", at, 45),
        _session("AM1", at + timedelta(minutes=55), 45),
        _session("EO", at + timedelta(minutes=110), 45, event_id=2),  # another subject between does not split AM1
        _session("AM1", at + timedelta(minutes=110), 45),
        _session("AM1", at + timedelta(hours=6), 45),  # hours later: a separate block
    ])
    assert [(t, a, b) for t, a, b in blocks][0] == ("AM1", at, at + timedelta(minutes=155))
    assert [b[0] for b in blocks] == ["AM1", "EO", "AM1"]


def test_voices_are_listed_without_a_desktop_agent(client, monkeypatch):
    monkeypatch.setattr(speech, "engine_voices", lambda: ["F1", "F2"])
    assert client.get("/api/v1/assistant/status").json()["voice"]["voices"] == ["F1", "F2"]


def test_no_speech_service_means_no_voices(monkeypatch):
    def down(*a, **k):
        raise speech.httpx.ConnectError("down")
    monkeypatch.setattr(speech.httpx, "get", down)
    monkeypatch.setattr(speech, "_engine", (0.0, []))
    assert speech.engine_voices() == []


def test_hearing_a_voice_in_settings_works_when_spoken_replies_are_off(client, monkeypatch):
    sent = {}
    def post(url, json, timeout):
        sent.update(json)
        return SimpleNamespace(raise_for_status=lambda: None, content=b"RIFF")
    monkeypatch.setattr(speech.httpx, "post", post)
    prefs = client.get("/api/v1/preferences/voice").json()
    client.put("/api/v1/preferences/voice", json=prefs | {"speak": False})
    assert client.post("/api/v1/voice/say", json={"text": "olá"}).status_code == 204
    r = client.post("/api/v1/voice/say", json={"text": "olá", "voice": "F3"})
    assert r.status_code == 200 and sent["voice"] == "F3"
    assert client.post("/api/v1/voice/say", json={"text": "olá", "voice": "../x"}).status_code == 422


def test_every_response_carries_the_security_headers(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "SAMEORIGIN"

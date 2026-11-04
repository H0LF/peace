from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from peace import assistant, llm, speech
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
    monkeypatch.setattr(speech, "engine_voices", lambda: ["tugao", "dii"])
    assert client.get("/api/v1/assistant/status").json()["voice"]["voices"] == ["tugao", "dii"]


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
    r = client.post("/api/v1/voice/say", json={"text": "olá", "voice": "dii"})
    assert r.status_code == 200 and sent["voice"] == "dii"
    assert client.post("/api/v1/voice/say", json={"text": "olá", "voice": "../x"}).status_code == 422


def test_every_response_carries_the_security_headers(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "SAMEORIGIN"


@pytest.mark.parametrize("said, command", [
    ("PEACE, o que tenho amanhã?", "day"), ("Você viu isso? O que tem amanhã?", "day"), ("Está visto o que tenho amanhã.", "day"), ("o que tenho hoje?", "day"), ("O que é que tenho na sexta?", "day"),
    ("quais são os meus prazos?", "deadlines"), ("que entregas tenho esta semana?", "deadlines"),
])
def test_plain_questions_are_answered_without_waiting_for_a_model(client, monkeypatch, said, command):
    monkeypatch.setattr(assistant.llm, "route", lambda *a: ("openai", "gpt-6"))
    monkeypatch.setattr(assistant, "llm_reachable", lambda *a: True)
    monkeypatch.setattr(assistant, "agent", lambda *a: pytest.fail("asked the model"))
    assert client.post("/api/v1/assistant/ask", json={"text": said, "source": "voice"}).json()["via"] == "rules"


@pytest.mark.parametrize("said", ["o que tenho de fazer para o projeto amanhã?", "que tempo vai estar hoje?", "o que há de novo no mundo hoje?", "diz-me uma coisa muito importante sobre isto, o que tenho hoje?"])
def test_anything_else_still_goes_to_the_model(client, monkeypatch, said):
    asked = []
    monkeypatch.setattr(assistant.llm, "route", lambda *a: ("openai", "gpt-6"))
    monkeypatch.setattr(assistant, "llm_reachable", lambda *a: True)
    monkeypatch.setattr(assistant, "agent", lambda *a: asked.append(a) or assistant.Reply("ok", "agent"))
    client.post("/api/v1/assistant/ask", json={"text": said, "source": "voice"})
    assert asked


def test_a_greeting_does_not_wait_for_a_model(client, monkeypatch):
    monkeypatch.setattr(assistant, "llm_reachable", lambda *a: True)
    monkeypatch.setattr(assistant, "chat", lambda *a: pytest.fail("asked the model"))
    assert client.post("/api/v1/assistant/ask", json={"text": "PEACE", "source": "voice"}).json()["intent"] == "help"


def test_an_action_before_the_question_is_not_ignored(client, monkeypatch):
    monkeypatch.setattr(assistant, "COMMANDS", assistant.COMMANDS | {"day": (lambda *a, **k: pytest.fail("read instead of acting"), {})})
    assert client.post("/api/v1/assistant/ask", json={"text": "apaga o que tenho hoje", "source": "voice"}).status_code == 200


def test_english_replies_are_translated_and_fall_back_to_portuguese(client, monkeypatch):
    sent = []
    monkeypatch.setattr(speech.httpx, "post", lambda url, json, timeout: sent.append(json) or SimpleNamespace(raise_for_status=lambda: None, content=b"RIFF"))
    prefs = client.get("/api/v1/preferences/voice").json()
    client.put("/api/v1/preferences/voice", json=prefs | {"language": "en", "voice": "joe"})
    monkeypatch.setattr(assistant, "llm_reachable", lambda *a: True)
    monkeypatch.setattr(assistant, "_say", lambda m: "You have a class.")
    client.post("/api/v1/voice/say", json={"text": "Tens uma aula."})
    assert (sent[-1]["input"], sent[-1]["language"], sent[-1]["voice"]) == ("You have a class.", "en", "joe")
    monkeypatch.setattr(assistant, "llm_reachable", lambda *a: False)
    client.post("/api/v1/voice/say", json={"text": "Tens uma aula."})
    assert (sent[-1]["input"], sent[-1]["language"]) == ("Tens uma aula.", "pt")
    client.post("/api/v1/voice/say", json={"text": "Hello", "voice": "cori"})
    assert (sent[-1]["language"], sent[-1]["voice"]) == ("en", "cori")


def test_a_reply_from_an_email_is_not_sent_to_the_cloud_to_be_translated(client, session, monkeypatch):
    from peace.models import AssistantMessage
    monkeypatch.setattr(assistant, "llm_reachable", lambda *a: True)
    monkeypatch.setattr(assistant, "_say", lambda m: "translated")
    monkeypatch.setattr(assistant.llm, "route", lambda purpose="chat": ("openai", "gpt-6") if purpose == "chat" else None)  # mail stays here
    session.add(AssistantMessage(role="assistant", content="O João escreveu-te.", intent="search_mail"))
    session.commit()
    assert client.post("/api/v1/voice/english", json={"text": "O João escreveu-te."}).json()["text"] is None
    session.add(AssistantMessage(role="assistant", content="Olá.", intent="help"))
    session.commit()
    assert client.post("/api/v1/voice/english", json={"text": "Olá."}).json()["text"] == "translated"


def test_the_heartbeat_accepts_what_a_voice_turn_reports(client):
    capabilities = {"audio": {"latency": {"stt_ms": 140, "stt_engine": "gpu", "tts_ms": 60}}}
    assert client.post("/api/v1/desktop/heartbeat", json={"capabilities": capabilities}).status_code < 300



def test_a_new_install_starts_with_nothing_connected_and_nobody_in_it(client, monkeypatch):
    monkeypatch.setattr(llm, "llm_reachable", lambda *a: False)  # not this PC's Ollama, whose answer would stay cached for later tests
    ai = client.get("/api/v1/ai").json()
    assert ai["active"]["provider"] in ("", None)
    assert not [p for p in ai["providers"] if p.get("connected") or p.get("has_key")]
    appearance = client.get("/api/v1/preferences/appearance").json()
    assert appearance["name"] == "" and appearance["assistant_name"] == "PEACE"

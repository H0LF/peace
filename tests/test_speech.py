import httpx
import pytest

from peace import decisions, speech


class Answer:
    def __init__(self, status=200, content=b"", json=None):
        self.status_code, self.content, self._json = status, content, json or {}
        self.is_error = status >= 400

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)


def test_hearing_falls_back_to_the_cpu_engine(client, monkeypatch):
    monkeypatch.setattr(speech, "STT_URL", "http://gpu")
    monkeypatch.setattr(speech, "VOICE_URL", "http://cpu")
    tried = []

    def post(url, **kw):
        tried.append(url)
        if url.startswith("http://gpu"):
            raise httpx.ConnectError("down")
        assert kw["files"]["file"][1] == b"RIFFaudio"
        return Answer(json={"text": " o que tenho hoje? "})

    monkeypatch.setattr(speech.httpx, "post", post)
    r = client.post("/api/v1/voice/hear", content=b"RIFFaudio", headers={"Content-Type": "audio/wav"})
    assert r.json() == {"text": "o que tenho hoje?"}
    assert [u.split("/v1")[0] for u in tried] == ["http://gpu", "http://cpu"]


def test_no_speech_service_is_a_clear_error(client, monkeypatch):
    def down(url, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(speech.httpx, "post", down)
    r = client.post("/api/v1/voice/hear", content=b"RIFF")
    assert r.status_code == 503 and "voz" in r.json()["detail"]
    assert client.post("/api/v1/voice/say", json={"text": "olá"}).status_code == 503


def test_too_much_audio_is_refused(client, monkeypatch):
    monkeypatch.setattr(speech, "MAX_AUDIO", 3)
    assert client.post("/api/v1/voice/hear", content=b"RIFFaudio").status_code == 413


def test_replies_are_spoken_in_words_and_respect_the_setting(client, monkeypatch):
    sent = {}

    def post(url, **kw):
        sent.update(kw["json"])
        return Answer(content=b"RIFFspeech")

    monkeypatch.setattr(speech.httpx, "post", post)
    r = client.post("/api/v1/voice/say", json={"text": "«Aula» às 17:00 e às 9:30"})
    assert r.content == b"RIFFspeech" and r.headers["content-type"] == "audio/wav"
    assert sent["input"] == "Aula às 17 horas e às 9 e 30"
    prefs = client.get("/api/v1/preferences/voice").json() | {"speak": False}
    client.put("/api/v1/preferences/voice", json=prefs)
    sent.clear()
    assert client.post("/api/v1/voice/say", json={"text": "olá"}).status_code == 204 and not sent


@pytest.mark.parametrize("path", ["/api/v1/voice/hear", "/api/v1/voice/say", "/api/v1/decide"])
def test_voice_and_decisions_need_the_token(path):
    from fastapi.testclient import TestClient

    from peace.api import app

    assert TestClient(app).post(path, json={}).status_code == 401


def test_decide_asks_laya_and_says_when_it_is_off(client, monkeypatch):
    question = {"urgent": {"type": "noul", "instructions": "É urgente?"}}

    def post(url, **kw):
        assert kw["json"] == {"state": {"text": "entrega amanhã"}, "questions": question}
        return Answer(json={"answers": {"urgent": {"noul": 0.9}}})

    monkeypatch.setattr(decisions.httpx, "post", post)
    assert client.post("/api/v1/decide", json={"state": "entrega amanhã", "questions": question}).json()["answers"]["urgent"]["noul"] == 0.9

    def down(url, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(decisions.httpx, "post", down)
    assert client.post("/api/v1/decide", json={"state": "x", "questions": question}).status_code == 503

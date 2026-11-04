"""PEACE's own speech service, with stand-in models (the real ones are downloaded only when used)."""

import io
import wave

import pytest

pytest.importorskip("multipart")  # the optional `voice` extra
np = pytest.importorskip("numpy")
from fastapi.testclient import TestClient  # noqa: E402

from peace import voice  # noqa: E402


def wav(samples, rate=44100):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.asarray(samples) * 32767).astype("<i2").tobytes())
    return out.getvalue()


class FakeWhisper:
    def transcribe(self, samples, language, vad_filter, initial_prompt=None):
        self.got = (samples, language)
        return [type("S", (), {"text": " olá "})()], None


class FakePiper:
    def __init__(self):
        self.said = []

    def synthesize_wav(self, text, w):
        self.said.append(text)
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\0\0" * 2205)


def test_speech_round_trip_routes(monkeypatch):
    whisper, tugao, dii, english = FakeWhisper(), FakePiper(), FakePiper(), FakePiper()
    monkeypatch.setattr(voice, "_models", {"stt": whisper, "tts:tugao": tugao, "tts:dii": dii, "tts:ljspeech": english})
    client = TestClient(voice.app)
    one_second = wav(np.zeros(44100))
    r = client.post("/v1/audio/transcriptions", files={"file": ("a.wav", one_second)}, data={"language": "pt"})
    assert r.json() == {"text": "olá"}
    samples, language = whisper.got
    assert language == "pt" and samples.dtype == np.float32 and len(samples) == 16000  # resampled to 16 kHz
    r = client.post("/v1/audio/speech", json={"input": "Olá", "voice": "F4", "language": "pt"})
    assert r.headers["content-type"] == "audio/wav" and tugao.said == ["Olá"]  # a voice it does not have: the default
    with wave.open(io.BytesIO(r.content)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 22050, 2205)
    client.post("/v1/audio/speech", json={"input": "Bom dia", "voice": "dii", "language": "pt"})
    client.post("/v1/audio/speech", json={"input": "Hello", "voice": "dii", "language": "en"})
    assert dii.said == ["Bom dia"] and english.said == ["Hello"]  # a Portuguese voice never reads English
    assert client.get("/health").json()["voices"] == ["tugao", "miro", "dii", "ljspeech", "cori", "joe", "mike"]
    assert client.post("/v1/audio/transcriptions", files={"file": ("a.mp3", b"ID3not a wav")}).status_code == 415


def test_the_language_decides_the_voice(monkeypatch):
    pt, en = FakePiper(), FakePiper()
    monkeypatch.setattr(voice, "_models", {"tts:tugao": pt, "tts:joe": en})
    client = TestClient(voice.app)
    client.post("/v1/audio/speech", json={"input": "olá", "voice": "joe", "language": "pt"})
    client.post("/v1/audio/speech", json={"input": "at 23", "voice": "joe", "language": "en"})
    assert pt.said == ["olá"] and en.said == ["at 23"]

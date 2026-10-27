"""PEACE's own speech service, with stand-in models (the real ones are downloaded only when used)."""

import io
import wave

import pytest

pytest.importorskip("multipart")  # the optional `voice` extra
np = pytest.importorskip("numpy")
from fastapi.testclient import TestClient  # noqa: E402

from peace import voice  # noqa: E402


def wav(samples, rate=44100):
    return voice.to_wav(np.asarray(samples, dtype=np.float32), rate)


class FakeWhisper:
    def transcribe(self, samples, language, vad_filter):
        self.got = (samples, language)
        return [type("S", (), {"text": " olá "})()], None


class FakeTTS:
    sample_rate = 44100

    def get_voice_style(self, voice_name):
        return voice_name

    def synthesize(self, text, voice_style, lang):
        self.got = (text, voice_style, lang)
        return np.zeros((1, 4410), dtype=np.float32), None


def test_speech_round_trip_routes(monkeypatch):
    whisper, tts = FakeWhisper(), FakeTTS()
    monkeypatch.setattr(voice, "_models", {"stt": whisper, "tts": tts})
    client = TestClient(voice.app)
    one_second = wav(np.zeros(44100))
    r = client.post("/v1/audio/transcriptions", files={"file": ("a.wav", one_second)}, data={"language": "pt"})
    assert r.json() == {"text": "olá"}
    samples, language = whisper.got
    assert language == "pt" and samples.dtype == np.float32 and len(samples) == 16000  # resampled to 16 kHz
    r = client.post("/v1/audio/speech", json={"input": "Hello", "voice": "M1", "language": "en"})
    assert r.headers["content-type"] == "audio/wav" and tts.got == ("Hello", "F1", "en")  # male voices are not offered
    with wave.open(io.BytesIO(r.content)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 44100, 4410)
    assert client.post("/v1/audio/transcriptions", files={"file": ("a.mp3", b"ID3not a wav")}).status_code == 415


def test_stereo_is_averaged_over_channels_not_time():
    stereo = np.stack([np.full(100, 0.5), np.full(100, -0.5)], axis=1)  # (time, channels)
    with wave.open(io.BytesIO(voice.to_wav(stereo, 16000))) as w:
        assert w.getnframes() == 100 and set(np.frombuffer(w.readframes(100), "<i2")) == {0}

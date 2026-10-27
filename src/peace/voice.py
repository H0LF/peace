"""PEACE's own speech service: Whisper (faster-whisper) hears, Supertonic 3 speaks, both on the CPU.

It answers the same OpenAI-style routes the desktop agent already uses with VoiceStudio, so either can run on
127.0.0.1:3900. Models download on first use (Hugging Face cache) and stay loaded. Optional extra: `voice`.
"""

import io
import os
import threading
import wave
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

STT_MODEL = os.environ.get("PEACE_STT_MODEL", "small")  # "medium" hears better, ~2x slower on the CPU
MAX_AUDIO = 10 * 1024 * 1024  # push-to-talk stops at 30 s: 16 kHz mono WAV is ~1 MB
VOICES = [f"F{i}" for i in range(1, 6)]

app = FastAPI(title="PEACE voice")
_models: dict[str, Any] = {}
_lock = threading.Lock()  # one model load, and one synthesis or transcription at a time


def _whisper():
    if "stt" not in _models:
        from faster_whisper import WhisperModel

        _models["stt"] = WhisperModel(STT_MODEL, device="cpu", compute_type="int8")
    return _models["stt"]


def _supertonic():
    if "tts" not in _models:
        from supertonic import TTS

        _models["tts"] = TTS(model="supertonic-3")  # the SDK pins the model revision
    return _models["tts"]


@app.get("/health")
def health():
    return {"ok": True, "stt": STT_MODEL, "voices": VOICES}


@app.post("/v1/audio/transcriptions")
def transcribe(file: UploadFile = File(...), language: str = Form("pt"), model: str = Form("")):
    audio = file.file.read(MAX_AUDIO + 1)
    if len(audio) > MAX_AUDIO:
        raise HTTPException(413, "audio too long")
    try:
        samples = from_wav(audio)
    except (wave.Error, EOFError) as e:
        raise HTTPException(415, "send 16-bit PCM WAV") from e
    with _lock:
        segments, _info = _whisper().transcribe(samples, language=language[:2] or None, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
    return {"text": text}


class SpeechIn(BaseModel):
    input: str = Field(min_length=1, max_length=2000)
    voice: str = "F1"
    language: str = Field("pt", pattern=r"^[a-z]{2}$")
    model: str = ""
    response_format: str = "wav"


@app.post("/v1/audio/speech")
def speak(body: SpeechIn):
    with _lock:
        tts = _supertonic()
        style = tts.get_voice_style(voice_name=body.voice if body.voice in VOICES else VOICES[0])
        samples, _duration = tts.synthesize(text=body.input, voice_style=style, lang=body.language)
    return Response(to_wav(samples, getattr(tts, "sample_rate", 44100)), media_type="audio/wav")


def from_wav(data: bytes) -> Any:
    """16-bit PCM WAV to the float32 mono 16 kHz samples Whisper takes (no ffmpeg/PyAV needed)."""
    import numpy as np

    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 2:
            raise wave.Error("not 16-bit PCM")
        rate, channels = w.getframerate(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    mono = pcm.reshape(-1, channels).mean(axis=1)
    if rate != 16000:  # linear resampling is plenty for speech recognition
        mono = np.interp(np.arange(0, len(mono), rate / 16000), np.arange(len(mono)), mono).astype(np.float32)
    return mono


def to_wav(samples: Any, rate: int) -> bytes:
    """Float samples in [-1, 1] (any shape with one long axis) to 16-bit mono WAV."""
    import numpy as np

    mono = np.asarray(samples, dtype=np.float32).squeeze()
    while mono.ndim > 1:
        mono = mono.mean(axis=int(np.argmin(mono.shape)))  # average the channel axis, never time
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes((np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes())
    return out.getvalue()


def main() -> None:
    uvicorn.run(app, host=os.environ.get("PEACE_VOICE_HOST", "127.0.0.1"), port=int(os.environ.get("PEACE_VOICE_PORT", "3900")))

"""PEACE's own speech service: Whisper (faster-whisper) hears, Piper speaks, both on the CPU, never the GPU.

It answers OpenAI-style routes on 127.0.0.1:3900. Models download once (Hugging Face cache), load when the service starts
so the first answer is as quick as the rest, and stay loaded. Optional extra: `voice` (piper-tts is GPL-3.0).
"""

import io
import logging
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
RHASSPY = ("rhasspy/piper-voices", "e7229f7d33a10e145e109b886d661984201ac3c4")
DII = ("OpenVoiceOS/pipertts_pt-PT_dii", "e3e45e9c0790a847d00937f15cfacb26e745a516")  # TigreGotico Lda, CC BY-NC-ND 4.0: downloaded as is, never changed
MIRO = ("OpenVoiceOS/pipertts_pt-PT_miro", "777a1e1587e95be8833fc99e8e035491c70fcd62")  # the same owner and licence
VOICES = {"tugao": (*RHASSPY, "pt/pt_PT/tugão/medium/pt_PT-tugão-medium.onnx"), "miro": (*MIRO, "miro_pt-PT.onnx"),
          "dii": (*DII, "dii_pt-PT.onnx")}  # European Portuguese
ENGLISH = {"ljspeech": (*RHASSPY, "en/en_US/ljspeech/high/en_US-ljspeech-high.onnx"), "cori": (*RHASSPY, "en/en_GB/cori/high/en_GB-cori-high.onnx"),
           "joe": (*RHASSPY, "en/en_US/joe/medium/en_US-joe-medium.onnx"), "mike": (*RHASSPY, "en/en_US/mike/medium/en_US-mike-medium.onnx")}
DEFAULT_VOICE, DEFAULT_ENGLISH = "tugao", "ljspeech"

log = logging.getLogger(__name__)
app = FastAPI(title="PEACE voice")
_models: dict[str, Any] = {}
_lock = threading.Lock()  # one model load, and one synthesis or transcription at a time


def _whisper():
    if "stt" not in _models:
        from faster_whisper import WhisperModel

        _models["stt"] = WhisperModel(STT_MODEL, device="cpu", compute_type="int8")
    return _models["stt"]


def _piper(name: str):
    key = "tts:" + name
    if key not in _models:
        from huggingface_hub import hf_hub_download
        from piper import PiperVoice

        repo, revision, path = (VOICES | ENGLISH)[name]
        hf_hub_download(repo, path + ".json", revision=revision)
        _models[key] = PiperVoice.load(hf_hub_download(repo, path, revision=revision))
    return _models[key]


def _warm() -> None:
    try:
        with _lock:
            _whisper()
            _piper(DEFAULT_VOICE)
    except Exception as e:  # no network yet, say: the first request tries again
        log.warning("speech models not ready: %s", type(e).__name__)


@app.get("/health")
def health():
    return {"ok": True, "stt": STT_MODEL, "voices": [*VOICES, *ENGLISH]}


@app.post("/v1/audio/transcriptions")
def transcribe(file: UploadFile = File(...), language: str = Form("pt"), model: str = Form(""), prompt: str = Form("", max_length=500)):
    audio = file.file.read(MAX_AUDIO + 1)
    if len(audio) > MAX_AUDIO:
        raise HTTPException(413, "audio too long")
    try:
        samples = from_wav(audio)
    except (wave.Error, EOFError) as e:
        raise HTTPException(415, "send 16-bit PCM WAV") from e
    with _lock:
        segments, _info = _whisper().transcribe(samples, language=language[:2] or None, vad_filter=True,
                                                 initial_prompt=prompt or None)
        text = " ".join(s.text.strip() for s in segments).strip()
    return {"text": text}


class SpeechIn(BaseModel):
    input: str = Field(min_length=1, max_length=2000)
    voice: str = DEFAULT_VOICE
    language: str = Field("pt", pattern=r"^[a-z]{2}$")
    model: str = ""
    response_format: str = "wav"


@app.post("/v1/audio/speech")
def speak(body: SpeechIn):
    # The language decides: a Portuguese voice cannot read English, nor the other way round.
    voices, fallback = (ENGLISH, DEFAULT_ENGLISH) if body.language == "en" else (VOICES, DEFAULT_VOICE)
    name = body.voice if body.voice in voices else fallback
    out = io.BytesIO()
    with _lock:
        tts = _piper(name)
        with wave.open(out, "wb") as w:
            tts.synthesize_wav(body.input, w)
    return Response(out.getvalue(), media_type="audio/wav")


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


def main() -> None:
    threading.Thread(target=_warm, daemon=True).start()
    uvicorn.run(app, host=os.environ.get("PEACE_VOICE_HOST", "127.0.0.1"), port=int(os.environ.get("PEACE_VOICE_PORT", "3900")))

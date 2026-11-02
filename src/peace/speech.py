"""Voice for the browser: the dashboard records and plays, this computer's speech services hear and speak.

The desktop agent uses the same services directly, but only with the microphone and speakers of the PC it runs on.
These routes let a browser on any device talk to PEACE, and keep the speech services on loopback.
"""

import os
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from peace import routes
from peace.auth import require_token
from peace.db import get_session

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])

VOICE_URL = os.environ.get("PEACE_VOICE_URL", "http://127.0.0.1:3900").rstrip("/")
STT_URL = os.environ.get("PEACE_STT_URL", VOICE_URL).rstrip("/")  # e.g. whisper.cpp on the GPU (peace-stt.service)
TTS = os.environ.get("PEACE_TTS", "supertonic3")
MAX_AUDIO = 10 * 1024 * 1024  # the dashboard stops at 30 s: 16 kHz mono WAV is ~1 MB
UNAVAILABLE = "A voz não está disponível neste servidor."
# Same hint as the desktop agent: no times or numbers, Whisper copies them.
STT_HINT = "Olá PEACE. Adiciona uma reunião. Marca uma consulta. Lembra-me de estudar. Cria uma tarefa. O que tenho hoje?"


def _transcribe(audio: bytes) -> str:
    for url in dict.fromkeys((STT_URL, VOICE_URL)):
        try:
            r = httpx.post(url + "/v1/audio/transcriptions", timeout=60, files={"file": ("fala.wav", audio, "audio/wav")},
                           data={"model": "whisper-1", "language": "pt", "prompt": STT_HINT})
            r.raise_for_status()
            return r.json().get("text", "").strip()
        except (httpx.HTTPError, ValueError):
            continue
    raise HTTPException(503, UNAVAILABLE)


@router.post("/voice/hear")
async def hear(request: Request):
    audio = bytearray()
    async for chunk in request.stream():
        audio += chunk
        if len(audio) > MAX_AUDIO:
            raise HTTPException(413, "Áudio demasiado longo")
    return {"text": await run_in_threadpool(_transcribe, bytes(audio))}


def for_speech(text: str) -> str:
    """Read times the way people say them (same rules as the desktop agent, which cannot import this package)."""
    hours = lambda n: f"{n} {'hora' if n == 1 else 'horas'}"
    text = re.sub(r"\b(\d{1,2}):00\b", lambda m: hours(int(m[1])), text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: f"{int(m[1])} e {int(m[2])}", text)
    text = re.sub(r"\b(\d+) h (\d+) min\b", lambda m: f"{hours(int(m[1]))} e {int(m[2])} minutos" if int(m[2]) else hours(int(m[1])), text)
    return text.replace("«", "").replace("»", "")


class SayIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/voice/say")
def say(body: SayIn, session: Session = Depends(get_session)):
    prefs = routes.get_voice(session)
    if not prefs.speak:
        return Response(status_code=204)
    # shortcut: always Portuguese; the agent's English voice translates through the model first. Share that if asked.
    speech = {"model": TTS, "input": for_speech(body.text)[:2000], "voice": prefs.voice, "language": "pt", "response_format": "wav"}
    try:
        r = httpx.post(VOICE_URL + "/v1/audio/speech", json=speech, timeout=120)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(503, UNAVAILABLE) from e
    return Response(r.content, media_type="audio/wav")

"""Voice for the browser: the dashboard records and plays, this computer's speech services hear and speak.

The desktop agent uses the same services directly, but only with the microphone and speakers of the PC it runs on.
These routes let a browser on any device talk to PEACE, and keep the speech services on loopback.
"""

import os
import re
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from peace import routes
from peace.auth import require_token
from peace.db import get_session

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])

VOICE_URL = os.environ.get("PEACE_VOICE_URL", "http://127.0.0.1:3900").rstrip("/")
STT_URL = os.environ.get("PEACE_STT_URL", VOICE_URL).rstrip("/")  # e.g. whisper.cpp on the GPU (peace-stt.service)
TTS = os.environ.get("PEACE_TTS", "piper")
MAX_AUDIO = 10 * 1024 * 1024  # the dashboard stops at 30 s: 16 kHz mono WAV is ~1 MB
UNAVAILABLE = "A voz não está disponível neste servidor."
# Same hint as the desktop agent: no times or numbers, Whisper copies them.
STT_HINT = "Olá PEACE. Adiciona uma reunião. Marca uma consulta. Lembra-me de estudar. Cria uma tarefa. O que tenho hoje?"


ENGLISH_VOICES = ["ljspeech", "cori", "joe", "mike"]
VOICES = ["tugao", "miro", "dii", *ENGLISH_VOICES]  # for a speech service whose /health does not list its voices
_engine: tuple[float, list[str]] = (0.0, [])
ENGINE_CHECK_SECONDS = 30.0


def engine_voices() -> list[str]:
    """The voices of this server's speech service, [] when it is off. Settings polls: asked at most once in a while."""
    global _engine
    if time.monotonic() - _engine[0] > ENGINE_CHECK_SECONDS or not _engine[0]:
        try:
            r = httpx.get(VOICE_URL + "/health", timeout=1)
            r.raise_for_status()
            _engine = (time.monotonic(), r.json().get("voices") or VOICES)
        except (httpx.HTTPError, ValueError, AttributeError):
            _engine = (time.monotonic(), [])
    return _engine[1]


def _transcribe(audio: bytes, name: str) -> str:
    for url in dict.fromkeys((STT_URL, VOICE_URL)):
        try:
            r = httpx.post(url + "/v1/audio/transcriptions", timeout=60, files={"file": ("fala.wav", audio, "audio/wav")},
                           data={"model": "whisper-1", "language": "pt", "prompt": STT_HINT.replace("PEACE", name)})
            r.raise_for_status()
            return r.json().get("text", "").strip()
        except (httpx.HTTPError, ValueError):
            continue
    raise HTTPException(503, UNAVAILABLE)


def _assistant_name(session: Session) -> str:
    return routes.get_appearance(session).assistant_name


@router.post("/voice/hear")
async def hear(request: Request, session: Session = Depends(get_session)):
    audio = bytearray()
    async for chunk in request.stream():
        audio += chunk
        if len(audio) > MAX_AUDIO:
            raise HTTPException(413, "Áudio demasiado longo")
    return {"text": await run_in_threadpool(_transcribe, bytes(audio), await run_in_threadpool(_assistant_name, session))}


def for_speech(text: str) -> str:
    """Read times the way people say them (same rules as the desktop agent, which cannot import this package)."""
    hours = lambda n: f"{n} {'hora' if n == 1 else 'horas'}"
    text = re.sub(r"\b(\d{1,2}):00\b", lambda m: hours(int(m[1])), text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: f"{int(m[1])} e {int(m[2])}", text)
    text = re.sub(r"\b(\d+) h (\d+) min\b", lambda m: f"{hours(int(m[1]))} e {int(m[2])} minutos" if int(m[2]) else hours(int(m[1])), text)
    return text.replace("«", "").replace("»", "")


PRIVATE_REPLIES = {"search_mail": "mail", "mail": "mail", "ask_material": "documents", "document": "documents", "materials": "documents"}  # intent: its sharing setting


def _portuguese(text: str) -> bool:
    return bool(re.search(r"[ãõçáéíóúâêô]|\b(tens|não|às|uma|para|que)\b", text, re.I))


def _english(session: Session, text: str) -> str | None:
    """The reply in spoken English, with the model in use. None when none answers, or when that would send away what its
    sharing setting keeps here: a reply may quote an email or a file, and hearing it must not change who can read it."""
    from peace import assistant, llm  # assistant imports this module
    from peace.models import AssistantMessage

    last = session.scalar(select(AssistantMessage.intent).where(AssistantMessage.role == "assistant").order_by(AssistantMessage.id.desc()).limit(1))
    if llm.route("chat") and not llm.route(PRIVATE_REPLIES.get(last or "", "chat")):
        return None
    if not assistant.llm_reachable():
        return None
    return assistant._say([{"role": "system", "content": "Translate the user message into natural spoken English. If it is already in English, repeat it. Reply with the translation only."},
                           {"role": "user", "content": text.replace("«", "").replace("»", "")}])


class EnglishIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/voice/english")
def english(body: EnglishIn, session: Session = Depends(get_session)):
    return {"text": _english(session, body.text)}


class SayIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    voice: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,40}$")  # hearing a voice in Settings, whatever is saved


@router.post("/voice/say")
def say(body: SayIn, session: Session = Depends(get_session)):
    prefs = routes.get_voice(session)
    if not prefs.speak and not body.voice:
        return Response(status_code=204)
    # Hearing a voice in Settings: its own language. A reply: the language chosen, when the model can put it in English.
    english = body.voice in ENGLISH_VOICES if body.voice else prefs.language == "en"
    text = body.text if body.voice else (_english(session, body.text) or body.text) if english else body.text
    if not body.voice and english and text == body.text and _portuguese(text):
        english = False  # could not translate: Portuguese read by a Portuguese voice beats nonsense
    speech = {"model": TTS, "input": for_speech(text)[:2000], "voice": body.voice or prefs.voice, "language": "en" if english else "pt", "response_format": "wav"}
    try:
        r = httpx.post(VOICE_URL + "/v1/audio/speech", json=speech, timeout=120)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(503, UNAVAILABLE) from e
    return Response(r.content, media_type="audio/wav")

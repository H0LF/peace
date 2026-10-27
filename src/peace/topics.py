"""What each course file covers, read once by the local model: its topics and what kind of document it is.

Runs in the worker whenever the model is reachable (Ollama, optional), a few
files per tick, and again only when a file changes. The text is untrusted data: it is sent as the user
message content, the answer is forced into a JSON schema, and only known document kinds are stored.
"""

import json
import logging
import time
from datetime import datetime

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from peace.llm import LLM_MODEL, LLM_OPTIONS, _llm, llm_reachable, mark_unreachable
from peace.models import Material

log = logging.getLogger(__name__)
KINDS = ("slides", "exercises", "past_exam", "statement", "solutions", "admin", "other")
MAX_CHARS = 9000  # the beginning says what a document is; ~3k tokens keeps a file at 1-3 s on the GPU
SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "doc_kind": {"type": "string", "enum": list(KINDS)},
    },
    "required": ["topics", "doc_kind"],
}
PROMPT = (
    "Lê o início deste documento de uma cadeira universitária e responde só com JSON.\n"
    "topics: até 8 temas concretos de matéria que o documento cobre, em português de Portugal, curtos (2-4 palavras), "
    "sem nomes de pessoas, datas nem salas.\n"
    "doc_kind: slides (apresentação de aula teórica), exercises (ficha de exercícios ou aula prática), "
    "past_exam (exame ou teste de anos anteriores), statement (enunciado de trabalho, projeto, laboratório ou meta a entregar), "
    "solutions (resolução de exercícios ou exames), admin (regras de avaliação, programa, bibliografia, horários, "
    "listas de alunos ou salas), other.\n\n"
    "Ficheiro: {name}\nCadeira: {course}\n---\n{text}"
)


def pending(session: Session, limit: int) -> list[Material]:
    """Analyzed files whose topics are missing or older than their current copy."""
    query = select(Material).where(
        Material.kind == "file",
        Material.content.is_not(None),
        Material.content != "",
        or_(Material.topics_at.is_(None), Material.topics_at < Material.fetched_at),
    )
    return list(session.scalars(query.order_by(Material.modified_at.desc().nulls_last()).limit(limit)))


def read_topics(m: Material) -> tuple[list[str], str] | None:
    """Ask the model; None when it is unreachable or answers outside the schema."""
    prompt = PROMPT.format(name=m.filename or m.title, course=m.course.name if m.course else "?", text=m.content[:MAX_CHARS])
    try:
        r = _llm("POST", "/api/chat", timeout=120, json={
            "model": LLM_MODEL, "stream": False, "think": "low", "format": SCHEMA, "keep_alive": "10m",
            "options": LLM_OPTIONS, "messages": [{"role": "user", "content": prompt}],
        })
        r.raise_for_status()
        data = json.loads(r.json()["message"]["content"])
    except httpx.HTTPStatusError as e:
        # 5xx (out of memory, loading) or 404 (model missing): the model's fault, not the file's.
        if e.response.status_code >= 500 or e.response.status_code == 404:
            mark_unreachable()
        log.warning("topics for %s failed: %s", m.filename, e)
        return None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("topics for %s failed: %s", m.filename, e)
        if isinstance(e, httpx.TransportError):
            mark_unreachable()
        return None
    if not isinstance(data, dict) or not isinstance(data.get("topics"), list):
        return None
    topics = [t.strip()[:60] for t in data["topics"] if isinstance(t, str) and t.strip()][:8]
    kind = data.get("doc_kind")
    return (topics, kind) if kind in KINDS else None


def analyze_pending(session: Session, now: datetime, limit: int = 10, budget_s: float = 60) -> int:
    """Read topics for up to `limit` files within `budget_s` seconds (the worker's tick also syncs and
    notifies). Returns how many were stored."""
    if not llm_reachable():
        return 0
    done, deadline = 0, time.monotonic() + budget_s
    for m in pending(session, limit):
        if time.monotonic() > deadline:
            break
        result = read_topics(m)
        if result is None:
            if not llm_reachable():
                break  # the PC went away; the rest waits for the next tick
            m.topics_at = now  # an answer outside the schema: don't retry this version forever
            m.topics, m.doc_kind, m.topics_model = [], "other", LLM_MODEL
        else:
            m.topics, m.doc_kind = result
            m.topics_model, m.topics_at = LLM_MODEL, now
            done += 1
        session.commit()
    return done

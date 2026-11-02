"""What a Google Calendar event is (class, exam or appointment) and which course it belongs to, from its text.

Rules first: they are instant, offline and right for the usual pt-PT titles. The local model only sees what the rules
cannot place, and anything it cannot answer is an appointment. Tests, frequências and mini-testes are kind "exam" (the
planner prepares for all of them alike); the title keeps the real name.
"""

import json
import re
import unicodedata
from collections.abc import Callable, Iterable

import httpx

from peace import llm

EXAM = re.compile(r"\b(exame|teste|mini ?teste|frequencia|prova|recurso|epoca (normal|especial|de recurso)|defesa|"
                  r"avaliacao|intercalar)s?\b")
CLASS = re.compile(r"\b(aula|teorica|pratica|laboratorio|seminario|tutoria|orientacao tutorial)s?\b")
CLASS_CODE = re.compile(r"\b(T|TP|PL|P|OT|S)\d{0,2}\b")  # timetable codes, case-sensitive: "P2", "TP3"
KINDS = ("class", "exam", "appointment")

Model = Callable[[str, str], str | None]


def fold(text: str) -> str:
    """Lowercase, no accents, hyphens as spaces: "Mini-Teste de Análise" -> "mini teste de analise"."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[\s\-_/]+", " ", plain)


def match_course(text: str, courses: Iterable[tuple[int, str | None, str]]) -> int | None:
    """The course whose code (whole word, exact case: "SO" but not "só") or full name appears; the longest name wins."""
    folded = fold(text)
    best: tuple[int, int] | None = None
    for course_id, code, name in courses:
        hit = (code and re.search(rf"\b{re.escape(code)}\b", text)) or fold(name) in folded
        length = len(name) if fold(name) in folded else len(code or "")
        if hit and (best is None or length > best[1]):
            best = (course_id, length)
    return best[0] if best else None


def classify(title: str, description: str, courses: Iterable[tuple[int, str | None, str]],
             model: Model | None = None) -> tuple[str, int | None]:
    """(kind, course_id) for an event. `model` is asked only when no rule decides; None means "no model"."""
    text = f"{title}\n{description}"
    folded = fold(text)
    course = match_course(text, list(courses))
    if EXAM.search(folded):
        return "exam", course
    if CLASS.search(folded) or CLASS_CODE.search(text) or course is not None:
        return "class", course
    answer = model(title, description) if model else None
    return (answer if answer in KINDS else "appointment"), course


def ask_model(title: str, description: str) -> str | None:
    """The local model's guess; None when it is unreachable or answers off-script."""
    if not llm.llm_reachable():
        return None
    prompt = ("Classifica este evento de calendário de um estudante universitário português. Responde só com o tipo: "
              "class (aula), exam (exame, teste, frequência, defesa, apresentação avaliada) ou appointment (outro).\n"
              f"Título: {title}\nDescrição: {description[:500]}")
    try:
        r = llm._llm("POST", "/api/chat", timeout=60, json={
            "model": llm.current_model(), "stream": False, "think": "low", "keep_alive": llm.KEEP_ALIVE,
            "options": llm.LLM_OPTIONS, "messages": [{"role": "user", "content": prompt}],
            "format": {"type": "object", "properties": {"kind": {"type": "string", "enum": list(KINDS)}},
                       "required": ["kind"]},
        })
        r.raise_for_status()
        kind = json.loads(r.json()["message"]["content"]).get("kind")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return kind if kind in KINDS else None

"""Concepts and Mastery (phase 6, ADR-022). Mastery comes from graded answers only; reading and time never count.

Concepts are curated: the model proposes a list per course from the topics already read in the course's files
(`topics.py`), each concept citing the topics it comes from (checked: a concept with no real topic is dropped);
the student keeps or drops each one (a Proposal). Prerequisites the model infers stay "inferred" until approved.

Estimate: Beta-Bernoulli per concept, prior Beta(1, 1). Each answer adds weight w·score to alpha and w'·(1-score)
to beta, where the weight halves every 30 days, an Estimated grade counts half until confirmed, and difficulty d
(0.5 easy … 2 hard) multiplies success by d and failure by 1/d: a hard question answered right says more, a hard
one answered wrong says less. The 80 % interval is the exact Beta quantile (no new dependency: continued fraction).
States: untested (no answers) · weak (mean < 0.5) · developing · mastered (lower bound ≥ 0.75, ≥ 3 answers on ≥ 2
days).
"""

import math
import re
import threading
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from peace.auth import require_token
from peace.db import get_engine, get_session
from functools import partial
from peace import llm
from peace.llm import pause_reason
from peace.mail_extract import containment
from peace.models import AuditEvent, Concept, ConceptAnswer, ConceptLink, Course, Material, Proposal
from peace.routes import get_now
ask_json = partial(llm.ask_json, purpose="documents")
llm_reachable = partial(llm.llm_reachable, purpose="documents")

HALF_LIFE = timedelta(days=30)
LOW, HIGH = 0.1, 0.9  # an 80 % interval
MASTERED_LOW, WEAK_MEAN, MIN_ANSWERS, MIN_DAYS = 0.75, 0.5, 3, 2
HYPHENS = re.compile("[\u2010\u2011\u2012\u2013\u2212]")  # the model writes odd hyphens ("Entidade‑Relação"): one kind
# gpt-oss writes Brazilian Portuguese even when told not to (seen 2026-10-29): fix the common, unambiguous words.
PT_PT = [  # longest first: the article or preposition changes with the noun's gender ("o banco" -> "a base")
    (r"num banco de dados", "numa base de dados"), (r"nos bancos de dados", "nas bases de dados"),
    (r"no banco de dados", "na base de dados"), (r"dos bancos de dados", "das bases de dados"),
    (r"do banco de dados", "da base de dados"), (r"os bancos de dados", "as bases de dados"),
    (r"o banco de dados", "a base de dados"), (r"bancos de dados", "bases de dados"), (r"banco de dados", "base de dados"),
    (r"dos relacionamentos", "das relações"), (r"do relacionamento", "da relação"), (r"os relacionamentos", "as relações"),
    (r"o relacionamento", "a relação"), (r"relacionamentos", "relações"), (r"relacionamento", "relação"),
    (r"em uma", "numa"), (r"em um", "num"), (r"controles", "controlos"), (r"controle", "controlo"), (r"conceitua(l|is)", r"conceptua\1"),
    (r"atômic([oa]s?)", r"atómic\1"), (r"usuários", "utilizadores"), (r"usuário", "utilizador"),
    (r"arquivos", "ficheiros"), (r"arquivo", "ficheiro"), (r"registros", "registos"), (r"registro", "registo"),
]


def pt_pt(text: str) -> str:
    """Brazilian words the model slips in, in their European Portuguese form (keeping a capital letter)."""
    for pattern, repl in PT_PT:
        text = re.sub(rf"\b{pattern}\b", lambda m, r=repl, p=pattern: _keep_case(m[0], re.sub(p, r, m[0].lower())), text, flags=re.I)
    return text


def _keep_case(original: str, new: str) -> str:
    return new[:1].upper() + new[1:] if original[:1].isupper() else new


MAX_TOPICS = 400  # one course's topic list fits the model's context with room to answer


# --- The estimate --------------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny, qab, qap, qam = 1e-300, a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1 + aa * d
            d = 1 / (d if abs(d) > tiny else tiny)
            c = 1 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1) < 1e-12:
            break
    return h


def beta_cdf(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    return front * _betacf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - front * _betacf(b, a, 1 - x) / b


def beta_quantile(p: float, a: float, b: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if beta_cdf(mid, a, b) < p else (lo, mid)
    return (lo + hi) / 2


def weight(answer: ConceptAnswer, now: datetime) -> float:
    w = 0.5 ** max(0.0, (now - answer.answered_at) / HALF_LIFE)
    return w * (0.5 if answer.grade == "estimated" and not answer.confirmed else 1.0)


def estimate(answers: list[ConceptAnswer], now: datetime) -> dict[str, Any]:
    if not answers:
        return {"state": "untested", "mean": None, "low": None, "high": None, "answers": 0, "evidence": []}
    a = b = 1.0
    evidence = []
    for ans in sorted(answers, key=lambda x: x.answered_at):
        w = weight(ans, now)
        a += w * ans.difficulty * ans.score
        b += w / ans.difficulty * (1 - ans.score)
        evidence.append({"source": ans.source, "score": ans.score, "difficulty": ans.difficulty, "grade": ans.grade,
                         "confirmed": ans.confirmed, "answered_at": ans.answered_at, "weight": round(w, 2)})
    mean, low, high = a / (a + b), beta_quantile(LOW, a, b), beta_quantile(HIGH, a, b)
    days = len({ans.answered_at.date() for ans in answers})
    state = ("mastered" if low >= MASTERED_LOW and len(answers) >= MIN_ANSWERS and days >= MIN_DAYS
             else "weak" if mean < WEAK_MEAN else "developing")
    return {"state": state, "mean": round(mean, 3), "low": round(low, 3), "high": round(high, 3),
            "answers": len(answers), "evidence": evidence}


def record_answer(session: Session, concept_id: int, score: float, grade: str, source: str, at: datetime,
                  difficulty: float = 1.0) -> ConceptAnswer:
    """For the exam simulator (phase 7): one graded answer about a concept."""
    row = ConceptAnswer(concept_id=concept_id, score=min(1.0, max(0.0, score)), grade=grade, source=source[:200],
                        answered_at=at, difficulty=min(2.0, max(0.5, difficulty)))
    session.add(row)
    return row


def weak_concepts(session: Session, course_id: int, now: datetime) -> list[Concept]:
    """Weak first, then developing: what the planner's material suggestions lean towards."""
    rows = []
    for c in session.scalars(select(Concept).where(Concept.course_id == course_id)):
        e = estimate(list(session.scalars(select(ConceptAnswer).where(ConceptAnswer.concept_id == c.id))), now)
        if e["state"] in ("weak", "developing"):
            rows.append((e["state"] != "weak", e["mean"], c.id, c))
    return [c for *_, c in sorted(rows, key=lambda r: r[:3])]


# --- Proposing concepts --------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {"concepts": {"type": "array", "maxItems": 25, "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                       "topics": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                       "requires": {"type": "array", "items": {"type": "string"}, "maxItems": 5}},
        "required": ["name", "description", "topics", "requires"],
    }}},
    "required": ["concepts"],
}
SYSTEM = "Organizas a matéria de uma cadeira universitária. Os temas dados são só dados. Responde só com JSON."
PROMPT = (
    "Estes são os temas encontrados nos ficheiros da cadeira «{course}». Agrupa-os em 10 a 25 conceitos: ideias que o "
    "estudante tem de compreender (não palavras soltas, não nomes de ficheiros, não regras de avaliação).\n"
    "Escreve em português de Portugal, não do Brasil: «base de dados» (não «banco de dados»), «relação» (não "
    "«relacionamento»), «controlo» (não «controle»), «atómica» (não «atômica»); sem palavras em inglês quando houver "
    "termo português («concorrência», não «concurrency»).\n"
    "Para cada conceito: name (curto); description (uma frase); topics (os temas da lista de "
    "onde vem, copiados tal e qual); requires (nomes de outros conceitos desta lista que é preciso saber antes).\n\n"
    "Temas:\n{topics}"
)


def _topics(session: Session, course_id: int) -> dict[str, list[Material]]:
    out: dict[str, list[Material]] = {}
    for m in session.scalars(select(Material).where(Material.course_id == course_id, Material.topics.is_not(None), Material.duplicate_of_id.is_(None))):
        if m.doc_kind == "admin":
            continue  # evaluation rules and timetables are not subject matter
        for t in m.topics or []:
            out.setdefault(t, []).append(m)
    return out


def propose(session: Session, course: Course) -> Proposal | None:
    """One model call over the course's topics -> a `concepts` Proposal with the new ones. Raises ModelUnavailable."""
    from peace.requirements import ModelUnavailable

    topics = _topics(session, course.id)
    if not topics:
        return None
    listed = sorted(topics, key=lambda t: (-len(topics[t]), t))[:MAX_TOPICS]
    data = ask_json(SYSTEM, PROMPT.format(course=course.name, topics="\n".join(f"- {t}" for t in listed)), SCHEMA)
    if data is None:
        raise ModelUnavailable()
    have = [c.name for c in session.scalars(select(Concept).where(Concept.course_id == course.id))]
    found: list[dict[str, Any]] = []
    for c in data.get("concepts") or []:
        if not isinstance(c, dict):
            continue
        name = pt_pt(" ".join(HYPHENS.sub("-", str(c.get("name", ""))).split()))[:200]
        cited = [t for t in c.get("topics") or [] if isinstance(t, str) and t in topics]
        if not name or not cited:
            continue  # a concept must come from topics really found in the files
        if any(containment(name, n) >= 0.75 for n in have + [f["name"] for f in found]):
            continue  # already there, or the same idea twice
        files = sorted({(m.id, m.filename or m.title) for t in cited for m in topics[t]})[:6]
        found.append({"name": name, "description": pt_pt(" ".join(HYPHENS.sub("-", str(c.get("description", ""))).split()))[:400],
                      "topics": cited, "files": [{"material_id": i, "file": f} for i, f in files],
                      "requires": [r for r in c.get("requires") or [] if isinstance(r, str)][:5]})
    if not found:
        return None
    key = f"concepts:{course.id}:{len(have)}:{','.join(sorted(f['name'] for f in found))}"[:200]
    if session.scalar(select(Proposal.id).where(Proposal.key == key)):
        return None
    for old in session.scalars(select(Proposal).where(Proposal.kind == "concepts", Proposal.status == "pending")):
        if old.evidence and old.evidence[0].get("course_id") == course.id:
            old.status = "superseded"
    proposal = Proposal(key=key, kind="concepts", title=f"Conceitos de {course.name}",
                        reason=f"{len(found)} conceitos tirados dos temas dos ficheiros da cadeira. Desmarca os que não fazem sentido; "
                               "as dependências entre eles ficam como «inferidas» até as aprovares.",
                        evidence=[{"source": "files", "ref": str(course.id), "course_id": course.id, "quote": course.name,
                                   "concepts": found}])
    session.add(proposal)
    session.flush()
    return proposal


def creates_cycle(session: Session, concept_id: int, requires_id: int) -> bool:
    """Would "concept needs requires" close a loop (requires already needs concept, directly or not)?"""
    seen, todo = set(), [requires_id]
    while todo:
        cur = todo.pop()
        if cur == concept_id:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        todo.extend(session.scalars(select(ConceptLink.requires_id).where(ConceptLink.concept_id == cur)))
    return False


def apply_concepts(session: Session, p: Proposal, skip: set[int]) -> dict[str, Any]:
    """Create the concepts the student kept; their prerequisites among the course's concepts, as inferred links."""
    ev = p.evidence[0]
    course_id = ev["course_id"]
    last = session.scalar(select(func.max(Concept.position)).where(Concept.course_id == course_id)) or 0
    made: list[Concept] = []
    for i, c in enumerate(ev["concepts"]):
        if i in skip or session.scalar(select(Concept.id).where(Concept.course_id == course_id, Concept.name == c["name"])):
            continue
        made.append(Concept(course_id=course_id, name=c["name"], description=c["description"], origin="model",
                            evidence=c["files"], position=last + len(made) + 1))
    session.add_all(made)
    session.flush()
    by_name = {c.name: c for c in session.scalars(select(Concept).where(Concept.course_id == course_id))}
    links = []
    for c in made:
        spec = next(x for x in ev["concepts"] if x["name"] == c.name)
        for name in spec["requires"]:
            other = by_name.get(name)
            if other and other.id != c.id and not creates_cycle(session, c.id, other.id):
                link = ConceptLink(concept_id=c.id, requires_id=other.id, approved=False)
                session.add(link)
                session.flush()
                links.append(link.id)
    return {"removed": [], "created": [], "concepts": [c.id for c in made], "links": links}


def undo_concepts(session: Session, p: Proposal) -> None:
    for i in p.result.get("concepts", []):
        if (c := session.get(Concept, i)) and not session.scalar(select(ConceptAnswer.id).where(ConceptAnswer.concept_id == i)):
            session.delete(c)  # a concept with answers keeps its evidence: the student removes it by hand


_proposing: dict[int, dict[str, Any]] = {}
_lock = threading.Lock()


def _propose_in_background(course_id: int) -> None:
    from peace.requirements import ModelUnavailable

    with Session(get_engine()) as session:
        try:
            made = propose(session, session.get(Course, course_id))
            session.commit()
            _proposing[course_id] = {"state": "done", "proposal": made.id if made else None}
        except ModelUnavailable:
            session.rollback()
            _proposing[course_id] = {"state": "error", "error": "O modelo local não respondeu; tenta outra vez daqui a pouco."}


# --- API -----------------------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
DB = Depends(get_session)
NOW = Depends(get_now)


def _course(session: Session, course_id: int) -> Course:
    course = session.get(Course, course_id)
    if course is None:
        raise HTTPException(404, "Cadeira não encontrada.")
    return course


def overview(session: Session, course: Course, now: datetime) -> dict[str, Any]:
    concepts = list(session.scalars(select(Concept).where(Concept.course_id == course.id).order_by(Concept.position, Concept.id)))
    ids = [c.id for c in concepts]
    answers: dict[int, list[ConceptAnswer]] = {}
    for a in session.scalars(select(ConceptAnswer).where(ConceptAnswer.concept_id.in_(ids))):
        answers.setdefault(a.concept_id, []).append(a)
    links = list(session.scalars(select(ConceptLink).where(ConceptLink.concept_id.in_(ids))))
    names = {c.id: c.name for c in concepts}
    return {"course": {"id": course.id, "name": course.name},
            "concepts": [{"id": c.id, "name": c.name, "description": c.description, "origin": c.origin, "files": c.evidence,
                          "confidence": c.confidence, "mastery": estimate(answers.get(c.id, []), now),
                          "requires": [{"link": link.id, "id": link.requires_id, "name": names.get(link.requires_id),
                                        "approved": link.approved} for link in links if link.concept_id == c.id]}
                         for c in concepts],
            "has_topics": bool(_topics(session, course.id)), "proposing": _proposing.get(course.id, {"state": "idle"}),
            "model": {"available": llm_reachable(), "paused": pause_reason()}}


@router.get("/courses/{course_id}/concepts")
def get_concepts(course_id: int, session: Session = DB, now: datetime = NOW):
    return overview(session, _course(session, course_id), now)


@router.post("/courses/{course_id}/concepts/propose", status_code=202)
def propose_concepts(course_id: int, background: BackgroundTasks, session: Session = DB):
    course = _course(session, course_id)
    if not _topics(session, course.id):
        raise HTTPException(409, "Os ficheiros desta cadeira ainda não foram lidos pelo modelo; volta a tentar mais tarde.")
    if not llm_reachable():
        raise HTTPException(409, f"O modelo local está em pausa ({pause_reason()})." if pause_reason() else "O modelo local não está disponível.")
    with _lock:
        if _proposing.get(course.id, {}).get("state") == "reading":
            return {"state": "reading"}  # a second click: the first proposal goes on
        _proposing[course.id] = {"state": "reading"}
    background.add_task(_propose_in_background, course.id)
    return {"state": "reading"}


class ConceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=400)


class ConceptPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=400)
    confidence: int | None = Field(None, ge=1, le=5)
    clear_confidence: bool = False


@router.post("/courses/{course_id}/concepts", status_code=201)
def add_concept(course_id: int, body: ConceptIn, session: Session = DB):
    course = _course(session, course_id)
    name = " ".join(body.name.split())
    if session.scalar(select(Concept.id).where(Concept.course_id == course.id, Concept.name == name)):
        raise HTTPException(409, "Esse conceito já existe nesta cadeira.")
    last = session.scalar(select(func.max(Concept.position)).where(Concept.course_id == course.id)) or 0
    row = Concept(course_id=course.id, name=name, description=body.description.strip(), origin="student", position=last + 1)
    session.add(row)
    session.add(AuditEvent(actor="user", action="concept.add", detail={"course": course.name, "concept": name}))
    session.commit()
    return {"id": row.id}


@router.patch("/concepts/{concept_id}")
def patch_concept(concept_id: int, body: ConceptPatch, session: Session = DB):
    row = session.get(Concept, concept_id)
    if row is None:
        raise HTTPException(404, "Conceito não encontrado.")
    if body.name is not None:
        row.name = " ".join(body.name.split())
    if body.description is not None:
        row.description = body.description.strip()
    if body.confidence is not None or body.clear_confidence:
        row.confidence = None if body.clear_confidence else body.confidence
    session.commit()
    return {"id": row.id}


@router.delete("/concepts/{concept_id}", status_code=204)
def delete_concept(concept_id: int, session: Session = DB):
    if row := session.get(Concept, concept_id):
        session.delete(row)
        session.commit()
    return Response(status_code=204)


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concept_id: int
    requires_id: int


@router.post("/concept-links", status_code=201)
def add_link(body: LinkIn, session: Session = DB):
    """The student writes or confirms "concept needs requires first"."""
    a, b = session.get(Concept, body.concept_id), session.get(Concept, body.requires_id)
    if a is None or b is None or a.course_id != b.course_id or a.id == b.id:
        raise HTTPException(422, "Escolhe dois conceitos diferentes da mesma cadeira.")
    link = session.scalar(select(ConceptLink).where(ConceptLink.concept_id == a.id, ConceptLink.requires_id == b.id))
    if link is None:
        if creates_cycle(session, a.id, b.id):
            raise HTTPException(409, f"«{b.name}» já depende de «{a.name}»: isso faria um círculo.")
        link = ConceptLink(concept_id=a.id, requires_id=b.id)
        session.add(link)
    link.approved = True
    session.commit()
    return {"id": link.id}


@router.delete("/concept-links/{link_id}", status_code=204)
def delete_link(link_id: int, session: Session = DB):
    if row := session.get(ConceptLink, link_id):
        session.delete(row)
        session.commit()
    return Response(status_code=204)

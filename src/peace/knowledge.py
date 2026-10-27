"""Knowledge layer: which files matter for what, and the graph that connects them. No LLM.

Relevance is TF-IDF cosine similarity over the key terms `library` extracted from each file.
The graph links courses, files, shared concepts (terms), tasks and exams. Links read directly from
the data are EXTRACTED; links computed from similarity are INFERRED (they can be wrong, and say so).
Graphify (graphifyy) adds community detection, its graph.json (for `graphify query/path/explain`)
and its Obsidian notes + Canvas, which the desktop copies into the vault's generated folder.
"""

import logging
import math
import re
import unicodedata
import shutil
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import networkx as nx
from sqlalchemy import select
from sqlalchemy.orm import Session

from peace.library import data_dir, term_frequencies
from peace.models import OPEN_STATUSES, Course, Event, Material, Task

log = logging.getLogger(__name__)
MIN_SCORE = 0.03


def _norm(vec: dict[str, float]) -> dict[str, float]:
    size = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {t: v / size for t, v in vec.items()}


class Index:
    """TF-IDF vectors of every analyzed file."""

    def __init__(self, materials: list[Material]):
        self.materials = {m.id: m for m in materials if m.terms}
        df = Counter(t for m in self.materials.values() for t in m.terms)
        n = len(self.materials)
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.vectors = {i: _norm({t: tf * self.idf[t] for t, tf in m.terms.items()}) for i, m in self.materials.items()}
        self._boilerplate: dict[int | None, set[str]] = {}
        # Topics the model read in each file, as terms (same folding as the text), kept per topic for the reasons.
        self.topics = {i: [(topic, set(term_frequencies(topic, top=10, min_count=1))) for topic in m.topics or []]
                       for i, m in self.materials.items()}

    def query(self, text: str) -> dict[str, float]:
        terms = term_frequencies(text, top=40, min_count=1)
        return _norm({t: tf * self.idf[t] for t, tf in terms.items() if t in self.idf})

    def rank(self, vec: dict[str, float], course_id: int | None, limit: int = 3) -> list[tuple[Material, float, list[str]]]:
        out = []
        for i, doc in self.vectors.items():
            m = self.materials[i]
            if course_id is not None and m.course_id != course_id:
                continue
            contributions = {t: w * doc[t] for t, w in vec.items() if t in doc}
            score = sum(contributions.values())
            if score >= MIN_SCORE:
                out.append((m, score, [t for t, _ in sorted(contributions.items(), key=lambda kv: -kv[1])[:3]]))
        return sorted(out, key=lambda r: -r[1])[:limit]

    def top_terms(self, material_id: int, n: int = 8) -> list[str]:
        """Most distinctive terms, minus the course's boilerplate (names, footers: in >40% of its files)."""
        course = self.materials[material_id].course_id
        if course not in self._boilerplate:
            docs = [m.terms for m in self.materials.values() if m.course_id == course]
            df = Counter(t for terms in docs for t in terms)
            self._boilerplate[course] = {t for t, c in df.items() if len(docs) >= 5 and c > 0.4 * len(docs)}
        ranked = sorted(self.vectors[material_id].items(), key=lambda kv: -kv[1])
        return [t for t, _ in ranked if t not in self._boilerplate[course]][:n]


def load_index(session: Session) -> Index:
    return Index(list(session.scalars(select(Material).where(Material.kind == "file", Material.terms.is_not(None)))))


# What a study session is for, from the task: preparing an exam, doing an assignment, or plain study.
EXAM = re.compile(r"\b(teste|exame|frequ[eê]ncia|mini-?teste|avalia[cç][aã]o escrita)", re.I)
ASSIGNMENT = re.compile(r"\b(entrega|submiss|meta\s?\d|projeto|trabalho|lab|relat[oó]rio|ficha \d)", re.I)
# How useful each kind of document is for each purpose (1 = neutral). Admin documents are never suggested.
KIND_WEIGHT = {
    "exam": {"past_exam": 1.6, "exercises": 1.4, "solutions": 1.3, "slides": 1.25, "statement": 0.6},
    "assignment": {"statement": 1.7, "slides": 1.0, "exercises": 1.0, "solutions": 0.8, "past_exam": 0.6},
    "study": {},
}
EXAM_KINDS = ("past_exam", "exercises", "slides", "solutions")
GENERIC_STUDY_TERMS = {'grupo', 'grupos', 'trabalho', 'trabalhos', 'aluno', 'alunos', 'cadeira',
                       'disciplina', 'material', 'materiais', 'ficheiro', 'semana', 'ano', 'avaliacao'}


def course_acronym(name: str) -> str:
    """ "Métodos de Otimização e Análise de Dados" -> "moad" (initials of the words longer than 2 letters)."""
    words = re.findall(r"\w+", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower())
    return "".join(w[0] for w in words if len(w) > 2)


def purpose(title: str, kind: str = "task") -> str:
    if EXAM.search(title):
        return "exam"
    return "assignment" if kind == "assignment" or ASSIGNMENT.search(title) else "study"


def _recent(m: Material) -> datetime:
    return m.modified_at or datetime.min.replace(tzinfo=UTC)


def suggest_text(index: Index, text: str, course_id: int | None, goal: str = "study", limit: int = 3,
                 title: str = "") -> list[dict[str, Any]]:
    """Files for studying `text` in a course, best first, each with a short pt-PT reason.

    Text similarity (TF-IDF) plus the topics the model read in each file, weighted by what the session
    is for. An exam with a vague title ("Teste 2") gets the course's exam material; anything else with
    nothing in common gets no suggestion rather than a guess."""
    if goal == 'exam' and course_id is None:
        return []
    # Administrative vocabulary is not evidence that a file teaches the requested subject.
    text = ' '.join(w for w in text.split() if not set(term_frequencies(w, min_count=1)) & GENERIC_STUDY_TERMS)
    if goal == "exam":  # "BD (Teste 1)": the course's acronym, the exam words and numbers say nothing about the topic
        course = next((m.course for m in index.materials.values() if m.course_id == course_id and m.course), None)
        names = {course_acronym(course.name), (course.code or "").lower()} - {""} if course else set()
        text = " ".join(w for w in re.sub(r"\d+", " ", EXAM.sub(" ", text)).split() if w.strip("()").lower() not in names)
    vec = index.query(text)
    strong = set(term_frequencies(text if goal == 'exam' else title or text, top=40, min_count=1)) - GENERIC_STUDY_TERMS
    weights = KIND_WEIGHT[goal]
    picks = []
    for i, doc in index.vectors.items():
        m = index.materials[i]
        if course_id is not None and m.course_id != course_id or m.doc_kind == "admin":
            continue
        contributions = {t: w * doc[t] for t, w in vec.items() if t in doc}
        covered = [topic for topic, terms in index.topics[i] if _covers(terms, vec.keys(), strong)]
        score = (sum(contributions.values()) + 0.12 * len(covered)) * weights.get(m.doc_kind or "", 1.0)
        if score >= MIN_SCORE:
            why = f"cobre {', '.join(covered[:2])}" if covered else f"fala de {', '.join(t for t, _ in sorted(contributions.items(), key=lambda kv: -kv[1])[:3])}"
            picks.append((score, m, why))
    if picks:
        picks.sort(key=lambda p: -p[0])
        # A weak tail is noise ("fala de grupo"): keep only files close to the best match.
        return [_ref(m, round(s, 3), why) for s, m, why in picks[:limit] if s >= 0.2 * picks[0][0]]
    if goal == "exam" and course_id is not None:
        exam_material = [m for m in index.materials.values() if m.course_id == course_id and m.doc_kind in EXAM_KINDS]
        if exam_material:  # past exams first, then exercises, then the most recent slides
            ranked = sorted(exam_material, key=lambda m: (EXAM_KINDS.index(m.doc_kind), -_recent(m).timestamp()))
            return [_ref(m, 0.0, KIND_LABEL[m.doc_kind]) for m in ranked[:limit]]
    return []


def _covers(topic_terms: set[str], query: Any, strong: set[str]) -> bool:
    """A topic counts when the request shares a word pair with it, two of its words, or one word of the
    title ("Diagramas ER" for "Modelos ER"); a stray "grupos" in a long description is not enough."""
    shared = topic_terms & query
    return any(" " in t for t in shared) or len(shared) >= 2 or bool(shared & strong)


KIND_LABEL = {"past_exam": "exame de anos anteriores", "exercises": "exercícios da cadeira", "slides": "slides da cadeira",
              "solutions": "resoluções"}


def suggest(index: Index, task: Task, limit: int = 3) -> list[dict[str, Any]]:
    """Files to study for a task, best first, each with a short pt-PT reason."""
    return suggest_text(index, f"{task.title} {task.title} {task.description}", task.course_id, purpose(task.title, task.kind), limit, task.title)


def _ref(m: Material, score: float, why: str) -> dict[str, Any]:
    return {"id": m.id, "title": m.title, "filename": m.filename, "vault_path": m.vault_path, "url": m.url,
            "score": score, "why": why}


# --- graph -------------------------------------------------------------------------------------

def build_graph(session: Session, now: datetime, concepts_per_file: int = 8, max_concepts: int = 160) -> nx.Graph:
    index = load_index(session)
    courses = {c.id: c for c in session.scalars(select(Course))}
    G = nx.Graph()

    def course_node(course_id: int | None) -> str | None:
        if course_id is None or course_id not in courses:
            return None
        node = f"course:{course_id}"
        if node not in G:
            c = courses[course_id]
            G.add_node(node, label=c.name, file_type="course", kind="course", course_id=c.id, source_file="")
        return node

    # Concepts: terms among the top terms of at least two files (a single file's quirks are noise).
    tops = {i: index.top_terms(i, concepts_per_file) for i in index.materials}
    df = Counter(t for terms in tops.values() for t in terms)
    concepts = {t for t, c in df.most_common(max_concepts) if c >= 2}

    for i, m in index.materials.items():
        node = f"material:{i}"
        G.add_node(node, label=m.title, file_type="document", kind="material", course_id=m.course_id,
                   source_file=m.vault_path or "", material_id=i)
        if c := course_node(m.course_id):
            G.add_edge(c, node, relation="contains", confidence="EXTRACTED", weight=1.0)
        for t in tops[i]:
            if t in concepts:
                G.add_node(f"concept:{t}", label=t, file_type="concept", kind="concept", course_id=None, source_file="")
                G.add_edge(node, f"concept:{t}", relation="mentions", confidence="EXTRACTED",
                           weight=round(index.vectors[i][t], 3))
        # Two most similar files elsewhere (inferred).
        for other, score, terms in index.rank(index.vectors[i], None, 3):
            if other.id != i and score >= 0.25:
                G.add_edge(node, f"material:{other.id}", relation="similar", confidence="INFERRED",
                           weight=round(score, 3), why=", ".join(terms))

    for t in session.scalars(select(Task).where(Task.status.in_(OPEN_STATUSES))):
        node = f"task:{t.id}"
        G.add_node(node, label=t.title, file_type="task", kind="task", course_id=t.course_id, source_file="")
        if c := course_node(t.course_id):
            G.add_edge(c, node, relation="has_task", confidence="EXTRACTED", weight=1.0)
        for s in suggest(index, t):
            if s["score"] > 0:
                G.add_edge(node, f"material:{s['id']}", relation="study_with", confidence="INFERRED",
                           weight=s["score"], why=s["why"])

    for e in session.scalars(select(Event).where(Event.kind == "exam", Event.starts_at >= now)):
        node = f"exam:{e.id}"
        G.add_node(node, label=e.title, file_type="exam", kind="exam", course_id=e.course_id, source_file="")
        if c := course_node(e.course_id):
            G.add_edge(c, node, relation="has_exam", confidence="EXTRACTED", weight=1.0)
    return G


def communities(G: nx.Graph) -> dict[str, int]:
    """Graphify's Leiden communities, node -> community id."""
    from graphify.cluster import cluster

    if G.number_of_edges() == 0:
        return {n: i for i, n in enumerate(G.nodes)}
    return {node: cid for cid, nodes in cluster(G).items() for node in nodes}


def graph_json(G: nx.Graph, community: dict[str, int], course_id: int | None = None) -> dict[str, Any]:
    keep = set(G.nodes)
    if course_id is not None:
        core = {n for n, d in G.nodes(data=True) if d.get("course_id") == course_id}
        keep = core | {nb for n in core for nb in G.neighbors(n) if G.nodes[nb]["kind"] == "concept"}
    degree = dict(G.degree())
    notes = note_names(G)
    nodes = [
        {"id": n, "label": d["label"], "kind": d["kind"], "course_id": d.get("course_id"),
         "community": community.get(n), "degree": degree[n], "vault_path": d.get("source_file") or None,
         "note": f"{NOTES_DIR}/{notes[n]}.md"}
        for n, d in G.nodes(data=True) if n in keep
    ]
    links = [
        {"source": a, "target": b, "relation": d["relation"], "inferred": d["confidence"] == "INFERRED",
         "weight": d.get("weight", 1.0), "why": d.get("why")}
        for a, b, d in G.edges(data=True) if a in keep and b in keep
    ]
    return {"nodes": nodes, "links": links}


NOTES_DIR = "07 - Generated/Graphify"  # where the desktop copies the notes inside the vault


def note_names(G: nx.Graph) -> dict[str, str]:
    """Node -> the file stem Graphify's Obsidian export gives its note (same helpers, graphifyy is pinned)."""
    from graphify.export import _DEDUP_SUFFIX_RESERVE, _dedup_node_filenames, _obsidian_safe_stem, stem_filename_budget

    limit = stem_filename_budget(data_dir() / "graphify" / "obsidian", reserve=_DEDUP_SUFFIX_RESERVE)
    return _dedup_node_filenames(G, lambda label: _obsidian_safe_stem(label, limit))


def export_graphify(G: nx.Graph, community: dict[str, int]) -> Path:
    """graph.json for the graphify CLI, and Obsidian notes + Canvas in a staging folder.
    The staging folder is rebuilt from scratch; the desktop copies only .md/.canvas files from it."""
    from graphify.export import to_canvas, to_json, to_obsidian

    out = data_dir() / "graphify"
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[int, list[str]] = defaultdict(list)
    for node, cid in community.items():
        groups[cid].append(node)
    labels = {cid: _community_label(G, nodes) for cid, nodes in groups.items()}
    to_json(G, dict(groups), str(out / "graph.json"), force=True, community_labels=labels)
    staging = out / "obsidian"
    shutil.rmtree(staging, ignore_errors=True)
    to_obsidian(G, dict(groups), str(staging), community_labels=labels)
    to_canvas(G, dict(groups), str(staging / "PEACE.canvas"), community_labels=labels)
    return staging


def _community_label(G: nx.Graph, nodes: list[str]) -> str:
    """Name a community after its course, or its most connected concept."""
    courses = Counter(G.nodes[n].get("course_id") for n in nodes if G.nodes[n]["kind"] != "concept")
    course = next((c for c, _ in courses.most_common() if c is not None), None)
    for n in nodes:
        if G.nodes[n]["kind"] == "course" and G.nodes[n]["course_id"] == course:
            return G.nodes[n]["label"]
    concept = max((n for n in nodes if G.nodes[n]["kind"] == "concept"), key=G.degree, default=None)
    return G.nodes[concept]["label"] if concept else f"Grupo {len(nodes)}"

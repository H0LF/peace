"""How a course's files relate: copies of one file, and the files one tells the student to open.

A copy counts once in search, the graph and concepts, and stays listed under its own course. A reference comes from the
file's own words (a name the model must quote from the text, or a file name that really exists in the course), and the
difference matters: «abre dados.zip» is a file to open, while «submete nome_numero.zip» is something to hand in.
"""

import re
import unicodedata
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from peace.models import Material

SAME = 0.9  # shared five-word runs for two files to be versions of one document
TRANSLATION_SIZE = 0.8
DATASET_SUFFIXES = {".txt", ".csv", ".json", ".xml", ".tsv", ".log", ".dat"}
DATASET_SIZE = 20
DATASET_SAMPLES = 3
ENGLISH = re.compile(r"(?:^|[\W_])(?:en|eng|english|erasmus)(?:$|[\W_])", re.I)
MIN_TEXT = 200  # shorter ones are too alike to say anything
PAGE = re.compile(r"\[p\. \d+\]")
NAME_LIKE = re.compile(r"\.\w{1,8}\b")  # "moodle (Aula 04)" is a place, not a file
NAME = re.compile(r"[\w.()-]*\w\.(?:zip|rar|7z|pdf|docx?|pptx?|xlsx?|csv|txt|sql|py|c|h|cpp|java|ipynb|ggb|mat)\b", re.I)


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def _words(m: Material) -> list[str]:
    return re.findall(r"\w+", fold(PAGE.sub(" ", m.content or "")))


def _runs(words: list[str]) -> set[str]:
    return {" ".join(words[i:i + 5]) for i in range(0, max(1, len(words) - 4))}


def mark_duplicates(session: Session) -> int:
    """Within each course, the newest of two copies or near-identical versions stands for both. Returns how many copies there are."""
    rows = list(session.scalars(select(Material).where(Material.kind == "file", Material.content.is_not(None), Material.course_id.is_not(None))))
    courses: dict[int, list[tuple[Material, set[str], int]]] = {}
    for m in rows:
        if m.duplicate_kind == "dataset":
            continue
        words = _words(m)
        if len(" ".join(words)) >= MIN_TEXT:
            courses.setdefault(m.course_id, []).append((m, _runs(words), len(words)))
    count = 0
    for group in courses.values():
        group.sort(key=lambda g: (g[0].modified_at.timestamp() if g[0].modified_at else 0, g[0].id), reverse=True)
        kept: list[tuple[Material, set[str], int]] = []
        for m, runs, size in group:
            if m.duplicate_of_id and m.duplicate_kind == "translation":  # text cannot show this, and the model already said so
                primary = session.get(Material, m.duplicate_of_id)
                if primary and same_length(m, primary):
                    count += 1
                    continue
                m.duplicate_of_id, m.duplicate_kind = None, None  # different lengths: versions, however alike the model thought them
            match = next((k for k, k_runs, k_size in kept if min(size, k_size) / max(size, k_size) >= SAME and len(runs & k_runs) / len(runs | k_runs) >= SAME), None)
            m.duplicate_of_id, m.duplicate_kind = (match.id, "copy") if match else (None, None)
            count += bool(match)
            if not match:
                kept.append((m, runs, size))
    session.commit()
    return count


def same_length(a: Material, b: Material) -> bool:
    """A translation says about as much as its original; a later version of a statement often does not."""
    la, lb = len(a.content or ""), len(b.content or "")
    return bool(la and lb) and min(la, lb) / max(la, lb) >= TRANSLATION_SIZE


def english(m: Material) -> bool:
    return bool(ENGLISH.search(m.title or "") or ENGLISH.search(m.filename or ""))


def mark_translation(m: Material, primary: Material) -> None:
    m.duplicate_of_id, m.duplicate_kind = primary.id, "translation"


def mark_datasets(session: Session) -> int:
    """A ZIP of hundreds of small texts is a data set, not reading: a few samples stand for the rest, or film reviews would become course topics."""
    rows = session.scalars(select(Material).where(Material.parent_id.is_not(None), Material.kind == "file", Material.content.is_not(None), Material.content != "").order_by(Material.parent_id, Material.id)).all()
    groups: dict[tuple[int, str], list[Material]] = {}
    for m in rows:
        groups.setdefault((m.parent_id, PurePosixPath(m.filename or "").suffix.lower()), []).append(m)
    count = 0
    for (_, suffix), group in groups.items():
        if suffix not in DATASET_SUFFIXES or len(group) < DATASET_SIZE:
            continue
        for m in group[DATASET_SAMPLES:]:
            m.duplicate_of_id, m.duplicate_kind = group[0].id, "dataset"
            count += 1
    session.commit()
    return count


def literal_names(text: str) -> set[str]:
    return {n for n in NAME.findall(text)}


def resolve(session: Session, m: Material, found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Point each reference at the file it names in this course. Also adds any existing file the text names literally."""
    others = [o for o in session.scalars(select(Material).where(Material.kind == "file", Material.course_id == m.course_id, Material.id != m.id, Material.course_id.is_not(None)))
              if o.id != m.parent_id and o.parent_id != m.id]  # an archive and what it holds are shown as such already
    named = {fold(o.filename or ""): o for o in others if o.filename}
    stems = {fold(PurePosixPath(o.filename).stem): o for o in others if o.filename and len(PurePosixPath(o.filename).stem) >= 4}

    def target(name: str) -> Material | None:
        n = fold(PurePosixPath(name.replace("\\", "/")).name)
        return named.get(n) or stems.get(n) or stems.get(fold(PurePosixPath(n).stem))

    out: dict[str, dict[str, Any]] = {}
    for ref in found:
        if "://" in ref["name"] or not NAME_LIKE.search(ref["name"]):
            continue
        hit = target(ref["name"])
        if ref["action"] == "entregar":
            hit = None  # a name to give your own file, even if something similar exists
        out.setdefault(fold(ref["name"]), {"name": ref["name"], "why": ref.get("why", ""), "action": ref["action"], "material_id": hit.id if hit else None})
    for name in literal_names(m.content or ""):
        hit = target(name)
        if hit and fold(name) not in out:
            out[fold(name)] = {"name": name, "why": "", "action": "abrir", "material_id": hit.id}
    return list(out.values())[:30]


def refresh_literal(session: Session) -> None:
    """Without any model: files that name another file of the course. The full reading adds what only a reader can tell."""
    for m in session.scalars(select(Material).where(Material.kind == "file", Material.content.is_not(None), Material.content != "", Material.file_refs.is_(None), Material.course_id.is_not(None))):
        m.file_refs = resolve(session, m, [])
    session.commit()


def to_open(m: Material) -> list[dict[str, Any]]:
    return [r for r in m.file_refs or [] if r["action"] == "abrir"]


def announce(m: Material) -> str:
    """The sentence PEACE says when this file is opened. Empty when it needs nothing else."""
    names = [r["name"] for r in to_open(m)]
    return f" Este ficheiro diz-te para usares também: {', '.join(names[:5])}." if names else ""

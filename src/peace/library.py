"""Server-side copy of course files and their text (no LLM).

The worker downloads each new or changed file once into PEACE_DATA_DIR, extracts plain text and
counts key terms. The desktop copies files from here, so it never needs the sites' credentials.
File contents are untrusted: they are only read as data, never executed or rendered as HTML.
"""

import html
import logging
import os
import re
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from peace import sync
from peace.models import Material

log = logging.getLogger(__name__)
logging.getLogger("pypdf").setLevel(logging.ERROR)  # one line per quirk in a teacher's PDF is noise
MAX_BYTES = 200 * 1024 * 1024
MAX_TEXT = 200_000
RETRY_AFTER = timedelta(days=1)


def data_dir() -> Path:
    return Path(os.environ.get("PEACE_DATA_DIR", "data"))


def cached_file(material_id: int) -> Path:
    return data_dir() / "materials" / str(material_id)


# --- text extraction ---------------------------------------------------------------------------

PLAIN = {".txt", ".md", ".c", ".h", ".cpp", ".hpp", ".cc", ".py", ".java", ".js", ".ts", ".sql", ".csv", ".json",
         ".html", ".htm", ".xml", ".m", ".r", ".sh", ".yaml", ".yml", ".tex", ".rs", ".go", ".kt", ".cs"}
OFFICE = {".docx": "word/document", ".pptx": "ppt/slides/slide", ".xlsx": "xl/sharedStrings", ".odt": "content",
          ".odp": "content", ".ods": "content"}


def _natural(name: str) -> list:
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]


def extract_text(path: Path, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        import pypdf

        reader = pypdf.PdfReader(path)
        raw = "\n".join(page.extract_text() or "" for page in reader.pages[:400])
    elif ext in OFFICE:
        with zipfile.ZipFile(path) as z:
            parts = sorted((n for n in z.namelist() if n.startswith(OFFICE[ext]) and n.endswith(".xml")), key=_natural)
            # Read at most 5 MB per part: a crafted archive cannot exhaust memory.
            raw = " ".join(re.sub(r"<[^>]+>", " ", z.open(n).read(5_000_000).decode("utf-8", "ignore")) for n in parts)
    elif ext in PLAIN:
        data = path.read_bytes()[:2_000_000]
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError:
            raw = data.decode("latin-1")
        if ext in (".html", ".htm", ".xml"):
            raw = re.sub(r"<[^>]+>", " ", raw)
    else:
        return ""  # images, videos, archives...
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()[:MAX_TEXT]


# --- key terms ---------------------------------------------------------------------------------

STOPWORDS = set("""
a à às ao aos o os e é era são ser sendo foi foram de da das do dos em no na nos nas num numa um uma uns umas
para pra por pelo pela pelos pelas com sem sob que se não nao mais menos como mas ou nem já ja há ha quando onde
qual quais quem cujo seu sua seus suas meu minha teu tua nosso nossa ele ela eles elas eu tu nós nos vós você
vocês lhe lhes me te isto isso aquilo este esta estes estas esse essa esses essas aquele aquela aqueles aquelas
muito muita muitos muitas pouco cada todo toda todos todas outro outra outros outras mesmo mesma tal tais tem têm
ter tendo tinha pode podem poder deve devem fazer feito faz entre sobre também tambem até ate após apos depois
antes ainda assim então entao porque pois caso sim apenas só so bem vez vezes etc
the a an of and or to in is are was were be been being for on that this these those with as by at from it its
into than then there their they them we you your our which not can could if will would should may might each
other more most all any one two three such also using used use how what when where who why
exemplo exemplos figura fig slide slides página pagina páginas capítulo capitulo aula aulas lei
licenciatura engenharia informática informatica ano letivo semestre docente prof professor cap sec ver nota
pdf doc docx ppt pptx xls xlsx zip rar txt http https www com html moodle pt en ex vs nº pp ok aí la lo el si
an at by if in is it of on or to up we us me my no so do go he be as am
int char void return print printf scanf var let const def self true false null none include std main string len
float double else elif for while import class public private static new this end begin
range name status info exit long number type value list item size count
""".split())
TOKEN = re.compile(r"[a-zà-öø-ÿ][a-zà-öø-ÿ+#]*(?:-[a-zà-öø-ÿ]+)*(?![0-9])")
GLYPH = re.compile(r"brace|htip|arrowhead|parenleft|parenright")  # PDF math glyph names, not words


def _singular(word: str) -> str:
    """Light pt/en plural folding so "modelos" matches "modelo" and "relações" matches "relação"."""
    if word.endswith("ções"):
        return word[:-4] + "ção"
    if word.endswith("ões"):
        return word[:-3] + "ão"
    if len(word) > 5 and word.endswith(("res", "zes")):
        return word[:-2]  # valores -> valor, vezes -> vez
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def term_frequencies(text: str, top: int = 80, min_count: int = 2) -> dict[str, float]:
    """Relative frequency of content words and adjacent word pairs (bigrams)."""
    # Two-letter words count (acronyms like ER, IP, SO); the stopword list removes the common ones.
    words = [_singular(w) for w in TOKEN.findall(text.lower())
             if len(w) > 1 and w not in STOPWORDS and not GLYPH.search(w)]
    counts = Counter(words)
    counts.update(f"{a} {b}" for a, b in zip(words, words[1:]) if a != b)
    total = sum(counts.values()) or 1
    return {t: round(c / total, 6) for t, c in counts.most_common(top) if c >= min_count}


# --- fetching ----------------------------------------------------------------------------------


def pending(session: Session, now: datetime, limit: int = 40) -> list[Material]:
    query = select(Material).where(
        Material.kind == "file",
        # The university download endpoint marks files as consulted; indexing must not do that.
        Material.source != 'inforestudante',
        # Not yet downloaded, or changed upstream since (NULL-safe: files without a date are fetched once).
        or_(Material.fetched_at.is_(None), Material.fetch_error.is_not(None),
            Material.fetched_modified_at.is_distinct_from(Material.modified_at)),
        or_(Material.fetch_error.is_(None), Material.fetched_at < now - RETRY_AFTER),
    )
    return list(session.scalars(query.order_by(Material.modified_at.desc().nulls_last()).limit(limit)))


def _analyze(m: Material, path: Path, now: datetime) -> None:
    try:
        m.content = extract_text(path, m.filename or "")
    except Exception as e:  # a broken or hostile file must not stop the batch
        log.warning("could not read %s: %s", m.filename, e)
        m.content = ""
    m.terms = term_frequencies(m.content) if m.content else {}
    m.fetched_at, m.fetched_modified_at, m.fetch_error = now, m.modified_at, None


def _fail(m: Material, error: str, now: datetime) -> None:
    m.fetched_at, m.fetch_error = now, error[:500]


def fetch_pending(session: Session, now: datetime, limit: int = 40) -> int:
    """Download and analyze new or changed files. Returns how many were processed."""
    on = sync.enabled_sources(session)
    todo = [m for m in pending(session, now, limit) if m.source in on]  # a switched-off source is not logged into
    if not todo:
        return 0
    (data_dir() / "materials").mkdir(parents=True, exist_ok=True)
    for m in todo:
        if m.size and m.size > MAX_BYTES:
            _fail(m, "ficheiro maior que 200 MB", now)
        elif m.source == "moodle" and m.file_url:
            target = cached_file(m.id)
            try:
                response, client = sync.open_moodle_file(m.file_url)
                try:
                    with target.with_suffix(".part").open("wb") as f:
                        received = 0
                        for chunk in response.iter_bytes():
                            received += len(chunk)
                            if received > MAX_BYTES:
                                raise ValueError('ficheiro maior que 200 MB')
                            f.write(chunk)
                finally:
                    response.close()
                    client.close()
                target.with_suffix(".part").replace(target)
                _analyze(m, target, now)
            except Exception as e:
                target.with_suffix('.part').unlink(missing_ok=True)
                _fail(m, f"{type(e).__name__}: {e}", now)
    session.commit()
    return len(todo)


def remove_orphans(session: Session) -> int:
    """Delete cached files whose material no longer exists (the vault copy stays)."""
    folder = data_dir() / "materials"
    if not folder.is_dir():
        return 0
    known = {str(i) for i in session.scalars(select(Material.id))}
    orphans = [p for p in folder.iterdir() if p.is_file() and p.suffix != ".part" and p.name not in known]
    for p in orphans:
        p.unlink(missing_ok=True)
    return len(orphans)

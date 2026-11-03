"""What is inside a course's ZIP files: each member becomes a file PEACE can read, search and open.

An archive is untrusted. Nothing is ever executed or unpacked to a path taken from the archive: a member's bytes go to
PEACE's own numbered copy, within limits on count and size, so a crafted archive can neither escape nor fill the disk.
Other archive formats need programs PEACE does not ship; they keep their name only.
"""

import hashlib
import logging
import zipfile
from datetime import datetime
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from peace import library
from peace.models import Material

log = logging.getLogger(__name__)
MAX_MEMBERS = 300
MAX_MEMBER = 50 * 1024 * 1024
MAX_TOTAL = 300 * 1024 * 1024
MAX_DEPTH = 1  # a zip inside a zip, once
NEVER_KEPT = {".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".com", ".msi", ".scr", ".ps1", ".o", ".obj", ".class", ".pyc", ".lnk"}
JUNK = {"__MACOSX", ".git", ".idea", ".vs", ".vscode", "node_modules", "__pycache__", "cmake-build-debug", ".DS_Store", "Thumbs.db"}
UNREADABLE = "Arquivo que a PEACE não conseguiu abrir."


def is_zip(m: Material) -> bool:
    return (m.filename or "").lower().endswith(".zip")


def _wanted(info: zipfile.ZipInfo) -> bool:
    path = PurePosixPath(info.filename.replace("\\", "/"))
    return not info.is_dir() and not (info.flag_bits & 1) and not set(path.parts) & JUNK and not path.name.startswith("._") and path.suffix.lower() not in NEVER_KEPT


def expand(session: Session, m: Material, now: datetime, depth: int = 0) -> int:
    """Create or refresh this archive's members, drop those it no longer has. The archive's text lists them. Returns how many."""
    existing = {r.external_id: r for r in session.scalars(select(Material).where(Material.parent_id == m.id))}
    seen, names, total = set(), [], 0
    try:
        with zipfile.ZipFile(library.cached_file(m.id)) as z:
            for info in [i for i in z.infolist() if _wanted(i)][:MAX_MEMBERS]:
                if info.file_size > MAX_MEMBER or total + info.file_size > MAX_TOTAL:
                    continue
                with z.open(info) as f:
                    data = f.read(MAX_MEMBER + 1)  # the declared size can lie
                if len(data) > MAX_MEMBER:
                    continue
                total += len(data)
                name = PurePosixPath(info.filename.replace("\\", "/"))
                key = f"{m.id}/{hashlib.sha256(info.filename.encode()).hexdigest()[:24]}"
                row = existing.get(key)
                if row is None:
                    row = Material(source="archive", external_id=key, parent_id=m.id, kind="file")
                    session.add(row)
                row.course_id = m.course_id
                row.section = f"{m.section} / {m.filename}"[:200]
                row.title, row.filename, row.size = name.name[:300], name.name[:300], len(data)
                row.modified_at = m.modified_at
                session.flush()
                path = library.cached_file(row.id)
                path.parent.mkdir(parents=True, exist_ok=True)
                unchanged = path.is_file() and path.read_bytes() == data and row.content is not None
                if not unchanged:
                    path.write_bytes(data)
                    library._analyze(row, path, now)
                    row.topics_at = None  # new contents: to be read again
                row.fetched_at, row.fetched_modified_at = now, m.modified_at
                seen.add(key)
                names.append(str(name))
                if is_zip(row) and depth < MAX_DEPTH and not row.content:
                    expand(session, row, now, depth + 1)
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError) as e:  # broken, hostile or an unknown compression
        log.warning("could not open archive %s: %s", m.filename, type(e).__name__)
        m.content = UNREADABLE
        return 0
    for key, row in existing.items():
        if key not in seen:
            session.delete(row)
    m.content = "Este arquivo contém: " + ", ".join(names) if names else "Este arquivo está vazio ou só tem ficheiros que a PEACE não guarda."
    return len(names)


def expand_pending(session: Session, now: datetime) -> int:
    """Archives downloaded but not opened yet (a new version empties `content`)."""
    done = 0
    for m in session.scalars(select(Material).where(Material.kind == "file", Material.filename.ilike("%.zip"), Material.parent_id.is_(None),
                                                    (Material.content.is_(None)) | (Material.content == ""), Material.fetched_at.is_not(None))):
        if library.cached_file(m.id).is_file():
            done += expand(session, m, now)
    session.commit()
    return done

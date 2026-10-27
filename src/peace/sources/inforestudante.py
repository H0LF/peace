"""inforestudante (Nonio, IPC) adapter. Plain HTTP with a session cookie; no browser needed.

Pages used (read-only):
- ensino/init.do?menu=submissaoTrabalhos: "Minhas UCs" table with course name, code and class groups.
- alunos/horario.do?method=actualizaDados: the JSON the timetable page loads (classes and assessments).
"""

import html
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from peace.sources import AuthError, SourceError, text_of

BASE = os.environ.get("INFORESTUDANTE_URL", "").rstrip("/")  # a Nonio portal, e.g. https://inforestudante.example.pt/nonio
LISBON = ZoneInfo("Europe/Lisbon")
CODE_AT_END = re.compile(r"\((\d{5,})\)\s*$")
KINDS = {"aulas": "class", "avaliacoes": "exam"}


@dataclass
class Snapshot:
    courses: list[dict[str, Any]]
    events: list[dict[str, Any]]
    window_start: datetime
    window_end: datetime
    skipped: dict[str, int]
    materials: list[dict[str, Any]] = field(default_factory=list)


UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
NO_MODULE = "Materiais sem Módulo"


def parse_materials(page: str, course_code: str) -> list[dict[str, Any]]:
    """Rows of a course's "Material de Apoio" list (the HTML its page loads by AJAX). Files only."""
    out = []
    for part in re.split(r'id="link_seccao_\d+"[^>]*>', page)[1:]:
        module = text_of(part.split("</a>", 1)[0], 200)
        for mat_id, row in re.findall(r'<tr id="divLinhaMat(\d+)"[^>]*>(.*?)</tr>', part, re.S):
            ids = re.search(r'value="\d+_(\d+)_(\d+)"', row)
            cells = [text_of(re.sub(r"<script.*?</script>", "", c, flags=re.S)) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if not ids or "downloadFichMatApoio" not in row or len(cells) < 3:
                continue  # descriptions without a file
            detail = re.search(rf'id="lista{mat_id}"(.*?)</table>', part, re.S)
            info = text_of(detail.group(1)) if detail else ""
            size = re.search(r"Tamanho:\s*([\d.,]+)\s*(GB|MB|KB|B)\b", info)
            ext = re.search(r"Extensão:\s*(\.\w{1,8})", info)
            title, kind = cells[0][:300], cells[1]
            suffix = ext.group(1).lower() if ext else ""
            day = re.match(r"(\d{2})-(\d{2})-(\d{4})", cells[2])
            out.append({
                "external_id": f"{course_code}:{ids.group(1)}:{ids.group(2)}",
                "course_code": course_code,
                "kind": "file",
                "title": title,
                "section": kind if module.startswith(NO_MODULE) else module,
                "filename": title if title.lower().endswith(suffix) else title + suffix,
                "folder": None,
                "subpath": "",
                "size": int(float(size.group(1).replace(",", ".")) * UNITS[size.group(2)]) if size else None,
                "mimetype": None,
                "modified_at": datetime(int(day[3]), int(day[2]), int(day[1]), tzinfo=LISBON) if day else None,
                "url": None,
                "file_url": None,
            })
    return out


def parse_courses(page: str) -> list[dict[str, Any]]:
    # shortcut: regex over a stable server-rendered table; switch to an HTML parser if the layout changes.
    courses = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 3 or not text_of(cells[1]).isdigit():
            continue
        groups = [text_of(g) for g in re.findall(r"<label[^>]*>(.*?)</label>", cells[2], re.S)]
        courses.append({"code": text_of(cells[1]), "name": text_of(cells[0], 200), "groups": groups})
    return courses


def _local(value: str) -> datetime:
    fmt = "%Y-%m-%d %H:%M" if " " in value else "%Y-%m-%d"
    return datetime.strptime(value, fmt).replace(tzinfo=LISBON)


def parse_timetable(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events, skipped = [], {}
    for item in items:
        category = item.get("className") or "?"
        kind = KINDS.get(category)
        if kind is None:
            skipped[category] = skipped.get(category, 0) + 1
            continue
        title, _, rooms = (item.get("title") or "").partition(" - ")
        code = CODE_AT_END.search(item.get("description") or "")
        start = _local(item["start"])
        events.append({
            "external_id": f"{category}:{item['id']}",
            "kind": kind,
            "title": text_of(title, 300) or "(sem título)",
            "starts_at": start,
            "ends_at": _local(item["end"]) if item.get("end") else start,
            "location": text_of(rooms, 200) or None,
            "course_code": code.group(1) if code else None,
        })
    return events, skipped


class Inforestudante:
    def __init__(self, username: str, password: str, http: httpx.Client | None = None):
        self.username, self.password = username, password
        self.http = http or httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 PEACE"})

    def login(self) -> None:
        self.http.get(f"{BASE}/security/login.do")
        page = self.http.post(
            f"{BASE}/security/login.do?method=submeter",
            data={"username": self.username, "password": self.password, "tipoCaptcha": ""},
        )
        page.raise_for_status()
        if "security/logout.do" not in page.text:
            reason = "captcha required" if "captcha" in page.text.lower() else "login rejected"
            raise AuthError(f"inforestudante: {reason}")

    def courses(self) -> list[dict[str, Any]]:
        page = self.http.get(f"{BASE}/ensino/init.do", params={"menu": "submissaoTrabalhos"})
        page.raise_for_status()
        courses = parse_courses(page.text)
        if not courses:
            raise SourceError("inforestudante: course table not found (layout changed or session lost)")
        return courses

    def timetable(self, start: date, weeks: int) -> tuple[list[dict[str, Any]], date]:
        """Week by week until the first week outside the published teaching period (the server then
        answers with an HTML error page). Returns the items and the end of the range actually read."""
        # The JSON endpoint only answers after the timetable page was opened in this session.
        self.http.get(f"{BASE}/alunos/horario.do").raise_for_status()
        items: list[dict[str, Any]] = []
        for week in range(weeks):
            first = datetime.combine(start + timedelta(weeks=week), time(0), LISBON)
            r = self.http.get(
                f"{BASE}/alunos/horario.do",
                params={
                    "method": "actualizaDados",
                    "start": int(first.timestamp() * 1000),
                    "end": int((first + timedelta(weeks=1)).timestamp() * 1000),
                },
                headers={"Referer": f"{BASE}/alunos/horario.do", "Accept": "application/json"},
            )
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("application/json"):
                if week == 0:
                    raise SourceError("inforestudante: timetable unavailable for the current week")
                return items, first.date()
            items.extend(r.json())
        return items, start + timedelta(weeks=weeks)

    def material_lists(self) -> dict[str, str]:
        """Course code -> HTML of its "Material de Apoio" list (current year)."""
        index = self.http.get(f"{BASE}/ensino/init.do", params={"menu": "materialApoio"})
        index.raise_for_status()
        pages = {}
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", index.text, re.S):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            link = re.search(r'href="(init\.do\?args=\d+)"', row)
            if len(cells) < 2 or not link or not text_of(cells[1]).isdigit():
                continue
            course = self.http.get(f"{BASE}/ensino/{html.unescape(link.group(1))}")
            tag = re.search(r'<input[^>]*name="org\.apache\.struts\.taglib\.html\.TOKEN"[^>]*>', course.text)
            token = tag and re.search(r'value="([^"]+)"', tag.group(0))
            if not token:
                raise SourceError("inforestudante: material page without form token (layout changed?)")
            listing = self.http.post(
                f"{BASE}/ensino/listagemMateriaisApoio.do",
                params={"method": "calculaModoApresentacaoAjax", "agruparTipo": "MODULOS",
                        "ordenarTipo": "DATA_DA_ULTIMA_ALTERACAO", "ePrimeiraVez": "true"},
                data={"org.apache.struts.taglib.html.TOKEN": token.group(1), "agruparPor": "MODULOS",
                      "ordenarMateriaisPor": "DATA_DA_ULTIMA_ALTERACAO"},
                headers={"Referer": str(course.url), "X-Requested-With": "XMLHttpRequest"},
            )
            listing.raise_for_status()
            pages[text_of(cells[1])] = listing.text
        return pages

    def download_materials(self, wanted: dict[str, Path]) -> dict[str, str]:
        """Download files by external id ("code:material:file") into the given paths.
        Download links carry per-session tokens, so they are looked up again here.
        Note: inforestudante marks a downloaded material as "consulted"."""
        errors = {}
        lists = self.material_lists()
        for external_id, target in wanted.items():
            code, mat, file_id = external_id.split(":")
            link = re.search(rf'value="\d+_{mat}_{file_id}".*?href="(downloadFichMatApoio\.do\?args=\d+)"', lists.get(code, ""), re.S)
            if not link:
                errors[external_id] = "não encontrado na listagem"
                continue
            with self.http.stream("GET", f"{BASE}/ensino/{html.unescape(link.group(1))}") as r:
                if r.status_code != 200 or r.headers.get("content-type", "").startswith("text/html"):
                    errors[external_id] = f"download recusado ({r.status_code})"
                    continue
                with target.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        return errors

    def snapshot(self, today: date, weeks: int = 16) -> Snapshot:
        self.login()
        start = today - timedelta(days=today.weekday())
        raw, read_until = self.timetable(start, weeks)
        events, skipped = parse_timetable(raw)
        courses = self.courses()  # before the material pages: those switch the session's course context
        materials = [m for code, page in self.material_lists().items() for m in parse_materials(page, code)]
        return Snapshot(
            courses=courses,
            events=events,
            window_start=datetime.combine(start, time(0), LISBON),
            window_end=datetime.combine(read_until, time(0), LISBON),
            skipped=skipped,
            materials=materials,
        )

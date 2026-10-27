"""Moodle adapter using the official web-service API, the one the Moodle mobile app uses."""

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from peace.sources import AuthError, SourceError, text_of

BASE = os.environ.get("MOODLE_URL", "").rstrip("/")  # the school's Moodle, e.g. https://moodle.example.pt/moodle
CODE_PREFIX = re.compile(r"^\s*(\d{5,})\s*-\s*(.+)$")
YEAR_PREFIX = re.compile(r"^\s*\d{4}\s*-\s*")
GROUP_TOKEN = re.compile(r"\b(?:P|PL|TP|T)\d{1,2}\b")
# "Meta3 - Modelos ER na P3 - pdf" -> "Meta3 - Modelos ER": cut from the group token onwards.
GROUP_SUFFIX = re.compile(r"\s+(?:na\s+)?\b(?:P|PL|TP|T)\d{1,2}\b.*$")
LOOKBACK = timedelta(days=14)


PLUGINFILE = f"{BASE}/webservice/pluginfile.php/"


@dataclass
class Snapshot:
    courses: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    materials: list[dict[str, Any]] = field(default_factory=list)


def _ts(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value else None


def _own_url(url: str | None) -> str | None:
    return url if url and url.startswith(BASE + "/") else None


def parse_courses(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    courses = []
    for c in raw:
        if c.get("hidden") or not c.get("visible", 1):
            continue
        match = CODE_PREFIX.match(c["fullname"])
        name = match.group(2) if match else YEAR_PREFIX.sub("", c["fullname"])
        courses.append({
            "id": c["id"],
            "code": match.group(1) if match else None,
            "name": text_of(name, 200),
            "url": f"{BASE}/course/view.php?id={c['id']}",
        })
    return courses


def submitted_at(status: dict[str, Any] | None) -> datetime | None:
    attempt = (status or {}).get("lastattempt") or {}
    for key in ("submission", "teamsubmission"):
        sub = attempt.get(key) or {}
        if sub.get("status") == "submitted":
            return _ts(sub.get("timemodified"))
    return None


def assignments_in_window(raw: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Assignments with a due date no older than LOOKBACK; undated ones (e.g. absence forms) are skipped."""
    return [
        a | {"course": course["id"]}
        for course in raw.get("courses", [])
        for a in course["assignments"]
        if a.get("duedate") and _ts(a["duedate"]) >= now - LOOKBACK
    ]


def parse_assignments(assignments: list[dict[str, Any]], statuses: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "external_id": f"assign:{a['id']}",
            "moodle_course": a["course"],
            "title": text_of(a["name"], 300),
            "description": text_of(a.get("intro")),
            "due_at": _ts(a["duedate"]),
            "url": f"{BASE}/mod/assign/view.php?id={a['cmid']}",
            "submitted_at": submitted_at(statuses.get(a["id"])),
        }
        for a in assignments
    ]


def parse_action_events(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Deadlines of other activities (quizzes, ...); assignments come from mod_assign with submission status."""
    return [
        {
            "external_id": f"{e['modulename']}:{e['instance']}",
            "moodle_course": (e.get("course") or {}).get("id"),
            "title": text_of(e.get("activityname") or e["name"], 300),
            "description": "",
            "due_at": _ts(e["timesort"]),
            "url": _own_url(e.get("url")),
            "submitted_at": None,
        }
        for e in raw.get("events", [])
        if e.get("modulename") and e["modulename"] != "assign"
    ]


def parse_materials(course_id: int, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Files of resource/folder modules and url-module links the student can see."""
    out = []
    for section in sections:
        name = text_of(section.get("name"), 200) or "Geral"
        for mod in section.get("modules", []):
            if not mod.get("uservisible", True) or mod["modname"] not in ("resource", "folder", "url"):
                continue
            for c in mod.get("contents") or []:
                base = {"moodle_course": course_id, "section": name, "url": _own_url(mod.get("url")),
                        "modified_at": _ts(c.get("timemodified"))}
                if mod["modname"] == "url":
                    link = c.get("fileurl") or ""
                    if urlparse(link).scheme in ("http", "https"):
                        out.append(base | {"external_id": f"url:{mod['id']}", "kind": "link", "title": text_of(mod["name"], 300),
                                           "url": link, "file_url": None, "filename": None, "folder": None, "subpath": "",
                                           "size": None, "mimetype": None})
                elif c.get("type") == "file" and (c.get("fileurl") or "").startswith(PLUGINFILE):
                    folder = mod["modname"] == "folder"
                    out.append(base | {
                        "external_id": f"file:{mod['id']}:{c.get('filepath') or '/'}{c['filename']}",
                        "kind": "file",
                        "title": text_of(c["filename"] if folder else mod["name"], 300),
                        "file_url": c["fileurl"],
                        "filename": c["filename"],
                        "folder": text_of(mod["name"], 120) if folder else None,
                        "subpath": (c.get("filepath") or "/").strip("/"),
                        "size": c.get("filesize"),
                        "mimetype": c.get("mimetype"),
                    })
    return out


def collapse_group_copies(tasks: list[dict[str, Any]], groups: dict[int, set[str]]) -> list[dict[str, Any]]:
    """Teachers often publish one copy of an assignment per class group (P1..P8).
    Keep the copy for the student's group when known; otherwise merge the copies into one task."""
    buckets: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for t in tasks:
        buckets[(t["moodle_course"], GROUP_SUFFIX.sub("", t["title"]), t["due_at"])].append(t)
    out = []
    for (course, base, _), copies in buckets.items():
        if len(copies) == 1 or len({t["title"] for t in copies}) == 1:
            out.extend(copies)
            continue
        mine = groups.get(course, set())
        match = [t for t in copies if mine & set(GROUP_TOKEN.findall(t["title"]))]
        if len(match) == 1:
            out.append(match[0])
            continue
        first = min(copies, key=lambda t: t["external_id"])
        done = [t["submitted_at"] for t in copies if t["submitted_at"]]
        out.append(first | {"title": base, "url": f"{BASE}/course/view.php?id={course}", "submitted_at": max(done, default=None)})
    return out


class Moodle:
    def __init__(self, username: str, password: str, http: httpx.Client | None = None):
        self.username, self.password = username, password
        self.http = http or httpx.Client(timeout=60)
        self.token = ""

    def login(self) -> None:
        r = self.http.post(
            f"{BASE}/login/token.php",
            data={"username": self.username, "password": self.password, "service": "moodle_mobile_app"},
        )
        r.raise_for_status()
        data = r.json()
        if "token" not in data:
            raise AuthError(f"moodle: {data.get('errorcode', 'login rejected')}")
        self.token = data["token"]

    def call(self, function: str, **params: Any) -> Any:
        r = self.http.post(
            f"{BASE}/webservice/rest/server.php",
            data={"wstoken": self.token, "moodlewsrestformat": "json", "wsfunction": function, **params},
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("exception"):
            error = AuthError if data.get("errorcode") in ("invalidtoken", "accessexception") else SourceError
            raise error(f"moodle {function}: {data.get('errorcode')}")
        return data

    def snapshot(self, now: datetime) -> Snapshot:
        self.login()
        user = self.call("core_webservice_get_site_info")["userid"]
        courses = parse_courses(self.call("core_enrol_get_users_courses", userid=user))
        assignments = assignments_in_window(self.call("mod_assign_get_assignments"), now)
        # A failed status call fails the sync: guessing "not submitted" would reopen submitted work.
        statuses = {a["id"]: self.call("mod_assign_get_submission_status", assignid=a["id"]) for a in assignments}
        events = self.call(
            "core_calendar_get_action_events_by_timesort",
            timesortfrom=int((now - LOOKBACK).timestamp()),
            limitnum=50,
        )
        materials = [m for c in courses for m in parse_materials(c["id"], self.call("core_course_get_contents", courseid=c["id"]))]
        return Snapshot(courses, parse_assignments(assignments, statuses) + parse_action_events(events), materials)

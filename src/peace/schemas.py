from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

TaskStatus = Literal["TODO", "IN_PROGRESS", "BLOCKED", "COMPLETED", "CANCELLED"]
EventKind = Literal["class", "exam", "appointment"]
SessionStatus = Literal["planned", "done", "skipped"]


def _http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("must be an http(s) URL")
    return value


def _vault_relative(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("must be a path relative to the vault, without '..'")
    return value


Url = Annotated[str, Field(max_length=2000), AfterValidator(_http_url)]
NotePath = Annotated[str, Field(max_length=1000), AfterValidator(_vault_relative)]
Title = Annotated[str, Field(min_length=1, max_length=300)]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CourseIn(In):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(None, max_length=64)
    url: Url | None = None


class CoursePatch(In):
    name: str | None = Field(None, min_length=1, max_length=200)
    code: str | None = Field(None, max_length=64)
    url: Url | None = None


class CourseRef(Out):
    id: int
    name: str
    code: str | None


class CourseOut(CourseRef):
    url: str | None
    source: str


class TaskIn(In):
    title: Title
    description: str = Field("", max_length=20000)
    kind: Literal["task", "assignment"] = "task"
    course_id: int | None = None
    priority: int = Field(2, ge=1, le=4)
    due_at: AwareDatetime | None = None
    estimated_minutes: int | None = Field(None, ge=1, le=10000)
    url: Url | None = None
    obsidian_note: NotePath | None = None


class TaskPatch(In):
    title: Title | None = None
    description: str | None = Field(None, max_length=20000)
    course_id: int | None = None
    status: TaskStatus | None = None
    priority: int | None = Field(None, ge=1, le=4)
    due_at: AwareDatetime | None = None
    estimated_minutes: int | None = Field(None, ge=1, le=10000)
    progress: int | None = Field(None, ge=0, le=100)
    url: Url | None = None
    obsidian_note: NotePath | None = None


class TaskRef(Out):
    id: int
    title: str


class TaskOut(TaskRef):
    description: str
    kind: str
    course: CourseRef | None
    status: str
    priority: int
    due_at: datetime | None
    estimated_minutes: int | None
    progress: int
    snoozed_until: datetime | None
    postponed_count: int
    completed_at: datetime | None
    source: str
    url: str | None
    obsidian_note: str | None
    created_at: datetime
    updated_at: datetime


class PostponeIn(In):
    until: AwareDatetime


class EventIn(In):
    title: Title
    kind: EventKind
    course_id: int | None = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    location: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def _ordered(self):
        if self.ends_at < self.starts_at:
            raise ValueError("ends_at must not be before starts_at")
        return self


class EventPatch(In):
    title: Title | None = None
    kind: EventKind | None = None
    course_id: int | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    location: str | None = Field(None, max_length=200)


class EventOut(Out):
    id: int
    title: str
    kind: str
    course: CourseRef | None
    starts_at: datetime
    ends_at: datetime
    location: str | None
    source: str
    url: str | None


class SessionIn(In):
    task_id: int | None = None
    event_id: int | None = None
    course_id: int | None = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def _ordered(self):
        if self.task_id is not None and self.event_id is not None:
            raise ValueError('choose a task or an assessment, not both')
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class SessionPatch(In):
    status: SessionStatus | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None


class StudyMaterial(BaseModel):
    id: int
    title: str
    filename: str | None
    vault_path: str | None
    url: str | None
    score: float
    why: str


class SessionOut(Out):
    id: int
    task: TaskRef | None
    event: TaskRef | None
    course_id: int | None
    starts_at: datetime
    ends_at: datetime
    status: str
    reason: str
    progress_note: str | None = None
    materials: list[StudyMaterial] = []


class ChangeOut(Out):
    id: int
    created_at: datetime
    source: str
    entity: str
    entity_id: int | None
    kind: str
    summary: str
    detail: dict[str, Any]
    seen_at: datetime | None


class SeenIn(In):
    ids: list[int] | None = Field(None, description="Omit to mark every unseen change as seen")


class MaterialOut(Out):
    id: int
    course: CourseRef | None
    section: str
    title: str
    kind: str
    filename: str | None
    size: int | None
    mimetype: str | None
    modified_at: datetime | None
    url: str | None
    vault_path: str | None
    created_at: datetime


class NotificationOut(Out):
    id: int
    kind: str
    title: str
    body: str
    url: str
    actions: list[str]
    urgent: bool
    task_id: int | None
    session_id: int | None
    created_at: datetime
    deliver_after: datetime
    expires_at: datetime | None
    delivered_at: datetime | None


class SnoozeIn(In):
    minutes: int = Field(60, ge=5, le=1440)


class SyncRunOut(Out):
    id: int
    source: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    stats: dict[str, Any]

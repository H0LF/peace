from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

TASK_STATUSES = ("TODO", "IN_PROGRESS", "BLOCKED", "COMPLETED", "CANCELLED")
OPEN_STATUSES = ("TODO", "IN_PROGRESS", "BLOCKED")
PLANNABLE_STATUSES = ("TODO", "IN_PROGRESS")
EVENT_KINDS = ("class", "exam", "appointment")
SESSION_STATUSES = ("planned", "done", "skipped")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    # Stable constraint names so Alembic migrations stay reproducible.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Sourced:
    """Rows that may come from an external source. `source_data` is the last payload that source sent."""

    source: Mapped[str] = mapped_column(String(32), server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    source_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Course(Sourced, Timestamps, Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(64), index=True)
    class_groups: Mapped[str | None] = mapped_column(String(100))  # e.g. "P4, T2, TP2"


class Task(Sourced, Timestamps, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("source", "external_id"),
        CheckConstraint(_in("status", TASK_STATUSES), name="status_valid"),
        CheckConstraint("priority BETWEEN 1 AND 4", name="priority_range"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, server_default="")
    kind: Mapped[str] = mapped_column(String(32), server_default="task")
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(16), server_default="TODO", index=True)
    priority: Mapped[int] = mapped_column(SmallInteger, server_default="2")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer)
    progress: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    postponed_count: Mapped[int] = mapped_column(Integer, server_default="0")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    obsidian_note: Mapped[str | None] = mapped_column(Text)

    course: Mapped[Course | None] = relationship(lazy="selectin")


class Event(Sourced, Timestamps, Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("source", "external_id"),
        CheckConstraint(_in("kind", EVENT_KINDS), name="kind_valid"),
        CheckConstraint("ends_at >= starts_at", name="ends_after_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(16))
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    location: Mapped[str | None] = mapped_column(String(200))

    course: Mapped[Course | None] = relationship(lazy="selectin")


class Material(Sourced, Timestamps, Base):
    """A course file or link from Moodle. `vault_path` is where the desktop copies the file."""

    __tablename__ = "materials"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"), index=True)
    section: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(16))  # file | link
    filename: Mapped[str | None] = mapped_column(String(300))
    size: Mapped[int | None] = mapped_column(BigInteger)
    mimetype: Mapped[str | None] = mapped_column(String(150))
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    file_url: Mapped[str | None] = mapped_column(Text)  # needs the Moodle token: served via the API only
    vault_path: Mapped[str | None] = mapped_column(Text)
    # Server-side copy and analysis (see peace.library).
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetch_error: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    terms: Mapped[dict[str, float] | None] = mapped_column(JSONB)
    # Topics and document kind read by the local model (see peace.topics); redone when the file changes.
    topics: Mapped[list[str] | None] = mapped_column(JSONB)
    doc_kind: Mapped[str | None] = mapped_column(String(32))
    topics_model: Mapped[str | None] = mapped_column(String(64))
    topics_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    course: Mapped[Course | None] = relationship(lazy="selectin")


class StudySession(Timestamps, Base):
    __tablename__ = "study_sessions"
    __table_args__ = (
        CheckConstraint(_in("status", SESSION_STATUSES), name="status_valid"),
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"), index=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default="planned")
    reason: Mapped[str] = mapped_column(Text, server_default="")
    progress_credit: Mapped[int] = mapped_column(SmallInteger, server_default='0')

    task: Mapped[Task | None] = relationship(lazy="selectin")
    event: Mapped[Event | None] = relationship(lazy="selectin")

    @property
    def progress_note(self) -> str | None:
        if self.progress_credit is not None and self.progress_credit < 0:
            return 'Sessão anterior à atualização: progresso preservado; ajusta-o na tarefa se necessário.'
        return None


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class Change(Base):
    __tablename__ = "changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    source: Mapped[str] = mapped_column(String(32))
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssistantMessage(Base):
    """One turn of the conversation with the assistant (typed or spoken)."""

    __tablename__ = "assistant_messages"
    __table_args__ = (CheckConstraint("role IN ('user', 'assistant')", name="role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), server_default="text")  # text | voice
    intent: Mapped[str | None] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class Notification(Base):
    """A desktop notification, generated on the server and delivered when the desktop asks.
    `key` makes generation idempotent (one notification per task deadline window, per day, ...)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, server_default="")
    url: Mapped[str] = mapped_column(String(300), server_default="/")
    actions: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    urgent: Mapped[bool] = mapped_column(server_default=text("false"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("study_sessions.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deliver_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Preference(Base):
    __tablename__ = "preferences"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)

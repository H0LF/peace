from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Index,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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
LOG_ORIGINS = ("timer", "manual")
PROPOSAL_STATUSES = ("pending", "applied", "rejected", "superseded", "undone")
REQUIREMENT_KINDS = ("functional", "constraint", "deliverable", "grading")
REQUIREMENT_STATES = ("unverified", "verified", "missing")
GRADE_KINDS = ("verified", "estimated")
PAST_EXAM_STATES = ("new", "reading", "review", "confirmed", "error")
QUESTION_KINDS = ("normalization", "er_model", "sql_query", "sql_ddl", "sql_dml", "transactions", "concurrency",
                  "calculation", "proof", "code", "theory", "other")
QUESTION_TYPES = ("written", "mcq", "numeric", "code")
PRACTICE_MODES = ("practice", "timed", "weakness", "retake")
QUESTION_STATUSES = ("draft", "ok", "discarded")


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
    # Phase 5: the file that is this assignment's brief (student-confirmed), the version its requirements were read
    # from, and the local project folder the checks run on.
    brief_material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id", ondelete="SET NULL"))
    brief_version: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    project_path: Mapped[str | None] = mapped_column(Text)

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
    # An imported class or exam the user deleted: hidden everywhere (see peace.db), kept so the next sync does not
    # bring it back.
    cancelled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)

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
    # The full reading (see peace.improve): inside an archive, a copy of another file, what it says, the files it refers to.
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), index=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id", ondelete="SET NULL"))
    duplicate_kind: Mapped[str | None] = mapped_column(String(16))  # copy | translation
    summary: Mapped[str | None] = mapped_column(Text)
    file_refs: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    briefs_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # deliveries read (see peace.workload)

    course: Mapped[Course | None] = relationship(lazy="selectin")


class StudyGuide(Timestamps, Base):
    """What to study for one exam and for how long, read from the course's files by the model (see peace.workload).
    `signature` is the files it was read from: changed files mean a new guide."""

    __tablename__ = "study_guides"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), unique=True)
    minutes: Mapped[int] = mapped_column(Integer)
    topics: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    why: Mapped[str] = mapped_column(Text, server_default="")
    signature: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))


class StudyPlan(Timestamps, Base):
    """Preparation for one exam (see peace.studyplan). `topics`: what must be studied and for how long; the sessions
    are ordinary study sessions, so the week, the calendar and Google see them too."""

    __tablename__ = "study_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), unique=True)
    topics: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    basis: Mapped[str] = mapped_column(String(16))  # guide | concepts | files | none: where the topics came from
    assumptions: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    needed_minutes: Mapped[int] = mapped_column(Integer)
    placed_minutes: Mapped[int] = mapped_column(Integer)
    signature: Mapped[str] = mapped_column(String(64))  # the exam's date and the evidence: a change means replanning

    event: Mapped[Event] = relationship(lazy="selectin")


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
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("study_plans.id", ondelete="SET NULL"), index=True)
    topic: Mapped[str | None] = mapped_column(String(200))  # with a plan: what this session is about
    activity: Mapped[str | None] = mapped_column(String(16))  # learn | recall | practice | review
    plan_materials: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

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
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # sent to the phone (peace.phone)


class Preference(Base):
    __tablename__ = "preferences"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)


class GoogleLink(Base):
    """A PEACE row mirrored as an event in the user's "PEACE" Google calendar.

    `payload_hash` is the event body last sent or received, so only real changes are pushed; `etag` is Google's version,
    so an edit made in Google meanwhile is never overwritten blindly. `suppressed`: the user deleted a deadline in Google.
    """

    __tablename__ = "google_links"
    __table_args__ = (
        UniqueConstraint("entity", "entity_id"),
        UniqueConstraint("google_id"),
        CheckConstraint(_in("entity", ("event", "task", "session")), name="entity_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[int] = mapped_column(Integer)
    google_id: Mapped[str] = mapped_column(String(255))
    etag: Mapped[str] = mapped_column(String(255), server_default="")
    payload_hash: Mapped[str] = mapped_column(String(64), server_default="")
    suppressed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Proposal(Base):
    """A change PEACE suggests and the student applies or rejects (ADR-020). `evidence` says where it came from,
    `result` records what applying did, so it can be undone. `key` makes creation idempotent."""

    __tablename__ = "proposals"
    __table_args__ = (CheckConstraint(_in("status", PROPOSAL_STATUSES), name="status_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(32))  # replan
    status: Mapped[str] = mapped_column(String(16), server_default="pending", index=True)
    title: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(Text, server_default="")
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"), index=True)
    change_id: Mapped[int | None] = mapped_column(ForeignKey("changes.id", ondelete="SET NULL"))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[Task | None] = relationship(lazy="selectin")
    event: Mapped[Event | None] = relationship(lazy="selectin")


class StudyLog(Base):
    """A stretch of time the student actually studied, timed or entered by hand (not proof of learning).
    A running timer is the one timer row without `ended_at`; the partial unique index keeps it to one."""

    __tablename__ = "study_logs"
    __table_args__ = (
        CheckConstraint(_in("origin", LOG_ORIGINS), name="origin_valid"),
        CheckConstraint("ended_at IS NULL OR ended_at > started_at", name="ends_after_start"),
        CheckConstraint("ended_at IS NOT NULL OR origin = 'timer'", name="only_timer_runs"),
        Index("uq_study_logs_one_running", "origin", unique=True, postgresql_where=text("ended_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("study_sessions.id", ondelete="SET NULL"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    task: Mapped[Task | None] = relationship(lazy="selectin")
    course: Mapped[Course | None] = relationship(lazy="selectin")


class MailMessage(Base):
    """A message seen in the student's university mailbox (phase 4). Headers for every message; the body only for
    academic ones. `removed_*` records what PEACE moved to Deleted Items, so it can be put back or emptied later."""

    __tablename__ = "mail_messages"
    __table_args__ = (UniqueConstraint("folder", "uidvalidity", "uid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    folder: Mapped[str] = mapped_column(String(16))  # inbox | junk
    uidvalidity: Mapped[int] = mapped_column(BigInteger)
    uid: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[str | None] = mapped_column(String(998), index=True)
    thread: Mapped[str | None] = mapped_column(String(998))  # the first References / In-Reply-To id
    sender: Mapped[str] = mapped_column(String(320), server_default="")
    sender_name: Mapped[str] = mapped_column(String(200), server_default="")
    subject: Mapped[str] = mapped_column(Text, server_default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    size: Mapped[int] = mapped_column(Integer, server_default="0")
    seen: Mapped[bool] = mapped_column(Boolean, server_default="false")
    flagged: Mapped[bool] = mapped_column(Boolean, server_default="false")
    answered: Mapped[bool] = mapped_column(Boolean, server_default="false")
    bulk: Mapped[bool] = mapped_column(Boolean, server_default="false")  # mailing list, newsletter, no-reply
    notice: Mapped[bool] = mapped_column(Boolean, server_default="false")  # a Moodle/inforestudante notification copy
    academic: Mapped[bool] = mapped_column(Boolean, server_default="false")
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"), index=True)
    body: Mapped[str | None] = mapped_column(Text)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[str | None] = mapped_column(String(32))  # junk (automatic) | proposal:<id>
    trash_uidvalidity: Mapped[int | None] = mapped_column(BigInteger)
    trash_uid: Mapped[int | None] = mapped_column(BigInteger)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Phase 4b: when the model read it, and the dates it found that passed the checks (shown on Correio).
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    facts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    course: Mapped[Course | None] = relationship(lazy="selectin")


class Requirement(Timestamps, Base):
    """One thing an assignment asks for (phase 5). `evidence` points at the brief (file, page, quote); `origin` says
    whether the model read it or the student wrote it; `edited` keeps the student's wording across re-reads."""

    __tablename__ = "requirements"
    __table_args__ = (CheckConstraint(_in("kind", REQUIREMENT_KINDS), name="kind_valid"),
                      CheckConstraint(_in("state", REQUIREMENT_STATES), name="state_valid"))

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), server_default="unverified")
    verified_by: Mapped[str | None] = mapped_column(String(64))  # student | check:<id>
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    origin: Mapped[str] = mapped_column(String(8), server_default="model")  # model | student
    edited: Mapped[bool] = mapped_column(Boolean, server_default="false")


class Check(Timestamps, Base):
    """A command that checks the student's project, run in the Sandbox (ADR-023). Only commands the student wrote
    or approved run; a model's suggestion waits with `approved` false."""

    __tablename__ = "checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    command: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(8), server_default="student")  # student | model
    approved: Mapped[bool] = mapped_column(Boolean, server_default="false")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(8))  # passed | failed | error
    last_output: Mapped[str | None] = mapped_column(Text)


class Concept(Timestamps, Base):
    """An idea of a course the student must understand (phase 6, ADR-022): proposed by the model from the course's
    files and approved by the student, or written by the student. Not a keyword. `confidence` (1-5) is what the
    student says; it is shown beside Mastery, never mixed into it."""

    __tablename__ = "concepts"
    __table_args__ = (UniqueConstraint("course_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, server_default="")
    origin: Mapped[str] = mapped_column(String(8), server_default="model")  # model | student
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # files it comes from
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    confidence: Mapped[int | None] = mapped_column(SmallInteger)


class ConceptLink(Base):
    """`concept` needs `requires` first. Inferred by the model until the student approves it."""

    __tablename__ = "concept_links"
    __table_args__ = (UniqueConstraint("concept_id", "requires_id"), CheckConstraint("concept_id <> requires_id", name="not_self"))

    id: Mapped[int] = mapped_column(primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)
    requires_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"))
    approved: Mapped[bool] = mapped_column(Boolean, server_default="false")


class ConceptAnswer(Base):
    """One graded answer about a Concept: the only evidence of Mastery. `score` 0-1; `difficulty` 0.5 (easy) to 2
    (hard). An Estimated grade counts half until the student confirms it."""

    __tablename__ = "concept_answers"
    __table_args__ = (CheckConstraint(_in("grade", GRADE_KINDS), name="grade_valid"),
                      CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
                      CheckConstraint("difficulty >= 0.5 AND difficulty <= 2", name="difficulty_range"))

    id: Mapped[int] = mapped_column(primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float)
    difficulty: Mapped[float] = mapped_column(Float, server_default="1")
    grade: Mapped[str] = mapped_column(String(10))
    confirmed: Mapped[bool] = mapped_column(Boolean, server_default="false")
    source: Mapped[str] = mapped_column(String(200))  # what was answered, e.g. "exam:12:3"
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PastExam(Timestamps, Base):
    """Photos or a PDF of a past exam the student sent (phase 7). `pages` are the stored page images; the questions
    read from them stay drafts until the student confirms them against the photo."""

    __tablename__ = "past_exams"
    __table_args__ = (CheckConstraint(_in("state", PAST_EXAM_STATES), name="state_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(10), server_default="new")
    error: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # {file, name}


class ExamQuestion(Timestamps, Base):
    """A question of the bank: from a past exam (written, the photo page is its figure) or generated from the course's
    files (multiple choice or numeric, kept only when a blind second answer agrees). `answer` is never shown before
    the student answers."""

    __tablename__ = "exam_questions"
    __table_args__ = (CheckConstraint(_in("kind", QUESTION_KINDS), name="kind_valid"),
                      CheckConstraint(_in("qtype", QUESTION_TYPES), name="qtype_valid"),
                      CheckConstraint(_in("status", QUESTION_STATUSES), name="status_valid"),
                      CheckConstraint("difficulty >= 0.5 AND difficulty <= 2", name="difficulty_range"))

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    past_exam_id: Mapped[int | None] = mapped_column(ForeignKey("past_exams.id", ondelete="CASCADE"), index=True)
    concept_id: Mapped[int | None] = mapped_column(ForeignKey("concepts.id", ondelete="SET NULL"), index=True)
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    number: Mapped[str] = mapped_column(String(10), server_default="")
    weight: Mapped[float | None] = mapped_column(Float)  # percent of the exam
    kind: Mapped[str] = mapped_column(String(16), server_default="other")
    qtype: Mapped[str] = mapped_column(String(8), server_default="written")
    text: Mapped[str] = mapped_column(Text)
    printed: Mapped[str] = mapped_column(Text, server_default="")  # code, tables, example output printed with it
    asks: Mapped[str] = mapped_column(Text, server_default="")  # what the student must do, in one sentence
    page: Mapped[int | None] = mapped_column(Integer)  # index into the past exam's pages: the real figure
    options: Mapped[list[str] | None] = mapped_column(JSONB)
    # {"choice": i} | {"value": x, "tolerance": t} | {"language", "solution", "tests"} (code; for SQL `tests` is the
    # setup script and the solution's rows are the key) | {"rubric": [{"criterion", "points"}]} (written)
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text, server_default="")
    difficulty: Mapped[float] = mapped_column(Float, server_default="1")
    status: Mapped[str] = mapped_column(String(10), server_default="draft")
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")  # `text` is a column here


class PracticeAttempt(Base):
    """One practice session: the questions drawn, in order. Timed attempts end at `ends_at` (the server's clock)."""

    __tablename__ = "practice_attempts"
    __table_args__ = (CheckConstraint(_in("mode", PRACTICE_MODES), name="mode_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(10), server_default="practice")
    question_ids: Mapped[list[int]] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PracticeAnswer(Base):
    __tablename__ = "practice_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id"), CheckConstraint(_in("grade", GRADE_KINDS), name="grade_valid"),
                      CheckConstraint("score >= 0 AND score <= 1", name="score_range"))

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("practice_attempts.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("exam_questions.id", ondelete="CASCADE"), index=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    score: Mapped[float | None] = mapped_column(Float)  # None until graded (timed attempts, written answers)
    grade: Mapped[str] = mapped_column(String(10))
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # {"state": pending | grading | waiting | done, ...}: the Sandbox output (code), the rubric marks (written)
    feedback: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

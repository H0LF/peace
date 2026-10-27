"""Notification delivery policy without a database or desktop session."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from peace.models import Event, StudySession, Task
from peace.notifications import NotifyPrefs, important_change, rate_limited
from peace.routes import _notification_is_current

NOW = datetime(2026, 10, 26, 13, tzinfo=UTC)


def note(**values):
    return SimpleNamespace(kind="deadline", key="task:1:24h", task_id=1, session_id=None,
                           expires_at=NOW + timedelta(hours=1), **values)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def get(self, model, key):
        return self.rows.get((model, key))


@pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
def test_closed_task_reminder_is_stale(status):
    task = SimpleNamespace(status=status, due_at=NOW + timedelta(hours=1))
    assert not _notification_is_current(note(), Rows({(Task, 1): task}), NotifyPrefs(), NOW)


def test_postponed_deadline_and_disabled_category_are_stale():
    task = SimpleNamespace(status="TODO", due_at=NOW + timedelta(days=7))
    rows = Rows({(Task, 1): task})
    assert not _notification_is_current(note(), rows, NotifyPrefs(), NOW)
    task.due_at = NOW + timedelta(hours=1)
    assert _notification_is_current(note(), rows, NotifyPrefs(), NOW)
    assert not _notification_is_current(note(), rows, NotifyPrefs(deadlines=False), NOW)


def test_skipped_or_rescheduled_session_is_stale():
    n = SimpleNamespace(kind="session", key="session:1:start", task_id=None, session_id=1,
                        expires_at=NOW + timedelta(hours=1))
    study = SimpleNamespace(status="planned", ends_at=n.expires_at, event_id=None)
    rows = Rows({(StudySession, 1): study})
    assert _notification_is_current(n, rows, NotifyPrefs(), NOW)
    study.status = "skipped"
    assert not _notification_is_current(n, rows, NotifyPrefs(), NOW)
    study.status, study.ends_at = "planned", NOW + timedelta(hours=2)
    assert not _notification_is_current(n, rows, NotifyPrefs(), NOW)


def test_finished_task_does_not_generate_more_study_but_submission_reminder_remains():
    task = SimpleNamespace(status="IN_PROGRESS", progress=100, due_at=NOW + timedelta(hours=1))
    rows = Rows({(Task, 1): task})
    assert not _notification_is_current(SimpleNamespace(kind="session", task_id=1), rows, NotifyPrefs(), NOW)
    assert _notification_is_current(note(), rows, NotifyPrefs(), NOW)


def test_changed_or_removed_assessment_is_stale():
    n = SimpleNamespace(kind="exam", key="exam:1:24h", task_id=None, session_id=None,
                        expires_at=NOW + timedelta(hours=1))
    exam = SimpleNamespace(kind="exam", starts_at=n.expires_at)
    rows = Rows({(Event, 1): exam})
    assert _notification_is_current(n, rows, NotifyPrefs(), NOW)
    exam.starts_at += timedelta(days=1)
    assert not _notification_is_current(n, rows, NotifyPrefs(), NOW)
    assert not _notification_is_current(n, Rows({}), NotifyPrefs(), NOW)


def test_rate_limit_stops_streaming_once_batch_is_full():
    def queue():
        for _ in range(20):
            yield SimpleNamespace(kind="deadline", urgent=False)
        raise AssertionError("must not consume the rest of the queue")
    assert len(rate_limited(queue(), 0, NotifyPrefs())) == 20


def test_imminent_academic_changes_bypass_low_priority_throttle():
    urgent = SimpleNamespace(kind="changes", urgent=True)
    secondary = SimpleNamespace(kind="changes", urgent=False)
    assert rate_limited([secondary, urgent], 0, NotifyPrefs()) == [urgent]


def test_exam_moved_from_tomorrow_remains_an_important_change():
    change = SimpleNamespace(entity="event", entity_id=1, detail={"starts_at": [(NOW + timedelta(days=1)).isoformat(), (NOW + timedelta(days=7)).isoformat()]})
    event = SimpleNamespace(kind="exam", starts_at=NOW + timedelta(days=7))
    rows = Rows({(Event, 1): event})
    assert important_change(rows, change, NOW)
    event.kind = "class"
    assert not important_change(rows, change, NOW)


def test_deadline_change_urgency_uses_actual_dates_and_unknowns_honestly():
    change = SimpleNamespace(entity="task", entity_id=1, detail={})
    task = SimpleNamespace(status="TODO", due_at=None)
    rows = Rows({(Task, 1): task})
    assert not important_change(rows, change, NOW)
    task.due_at = NOW + timedelta(hours=3)
    assert important_change(rows, change, NOW)
    task.status = "COMPLETED"
    assert not important_change(rows, change, NOW)

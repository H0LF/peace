"""Academic context regressions: real policy/serialization with external persistence isolated."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from peace import academic, routes
from peace.models import Event, StudySession, Task
from peace.knowledge import Index, suggest_text
from peace.models import Course, Material
from peace.planner import PlanPrefs, assessment_targets
from peace.schemas import SessionIn, SessionOut

import pytest
from pydantic import ValidationError

NOW = datetime(2026, 10, 26, 21, 6, tzinfo=UTC)
LX = ZoneInfo('Europe/Lisbon')


def test_context_includes_synced_assessments_and_completed_prep():
    exam = Event(id=1, title='MOAD (Frequência 1)', kind='exam', course_id=1,
                 starts_at=NOW + timedelta(hours=20), ends_at=NOW + timedelta(hours=22))
    done = StudySession(event_id=1, starts_at=NOW - timedelta(hours=1), ends_at=NOW, status='done')
    db = Mock()
    db.scalars.side_effect = [[], [exam], [done]]
    target, = academic.targets(db, NOW, PlanPrefs())
    assert target.event_id == 1 and target.estimated_minutes == 60


def test_recommendation_uses_same_course_materials_and_reports_no_available_time(monkeypatch):
    exam = SimpleNamespace(id=1, title='MOAD (Frequência 1)', course_id=1, course=None,
                           starts_at=datetime(2026, 10, 27, 17, tzinfo=UTC))
    target, = assessment_targets([exam], {}, PlanPrefs())
    monkeypatch.setattr(academic, 'targets', lambda *args: [target])
    monkeypatch.setattr(academic.knowledge, 'load_index', lambda *args: object())
    suggestions = Mock(return_value=[{'id': 3, 'title': 'Exame anterior', 'why': 'exame de anos anteriores'}])
    monkeypatch.setattr(academic.knowledge, 'suggest_text', suggestions)
    db = Mock()
    db.scalars.side_effect = [[SimpleNamespace(starts_at=NOW, ends_at=exam.starts_at)], [], []]
    rec = academic.recommendation(db, NOW, PlanPrefs(), LX)
    assert rec['event_id'] == 1 and rec['task_id'] is None
    assert suggestions.call_args.args[2:4] == (1, 'exam')
    assert rec['available_minutes'] == 0 and rec['starts_at'] is None and rec['warnings']
    assert rec['readiness'] is None and rec['estimated_minutes'] is None


def test_recommendation_fits_free_window_before_assessment(monkeypatch):
    exam = SimpleNamespace(id=1, title='Avaliação', course_id=None, course=None,
                           starts_at=datetime(2026, 10, 27, 9, 30, tzinfo=UTC))
    monkeypatch.setattr(academic, 'targets', lambda *args: assessment_targets([exam], {}, PlanPrefs()))
    monkeypatch.setattr(academic.knowledge, 'load_index', lambda *args: object())
    db = Mock()
    db.scalars.side_effect = [[], [], []]
    rec = academic.recommendation(db, NOW, PlanPrefs(), LX)
    assert rec['starts_at'] == datetime(2026, 10, 27, 9, tzinfo=UTC)
    assert rec['minutes'] == rec['available_minutes'] == 30
    assert '30 minutos' in rec['text']
    assert rec['materials'] == [] and rec['warnings']


def test_session_can_reference_assessment_without_a_synthetic_task():
    event = Event(id=1, title='Avaliação', kind='exam')
    row = StudySession(id=1, event=event, event_id=1, task=None, course_id=None, status='planned',
                       reason='preparação', starts_at=NOW, ends_at=NOW + timedelta(minutes=30))
    assert SessionOut.model_validate(row).event.title == 'Avaliação'
    assert SessionOut.model_validate(row).task is None
    with pytest.raises(ValidationError):
        SessionIn(task_id=1, event_id=1, starts_at=NOW, ends_at=NOW + timedelta(minutes=30))


def test_planning_preserves_manual_commitments_and_does_not_duplicate(monkeypatch):
    now = NOW.replace(hour=8, minute=0)
    task = Task(id=1, title='Trabalho', course_id=None, status='TODO', estimated_minutes=60,
                progress=0, postponed_count=0, priority=2)
    manual = StudySession(id=1, task_id=1, starts_at=now + timedelta(hours=1),
                          ends_at=now + timedelta(hours=2), status='planned')
    monkeypatch.setattr(academic, 'targets', lambda *args: [task])
    monkeypatch.setattr(routes, '_prefs', lambda *args: PlanPrefs())
    monkeypatch.setattr(routes, '_tz', lambda: LX)
    db = Mock()
    kept = Mock()
    kept.all.return_value = [manual]
    events = Mock()
    events.all.return_value = []
    db.scalars.side_effect = [[manual], kept, events]
    routes._plan(db, [now.date()], now)
    assert db.execute.call_count == 1
    assert 'pg_advisory_xact_lock' in str(db.execute.call_args.args[0])
    assert db.add_all.call_args.args == ([],)
    db.commit.assert_called_once()


def test_exam_materials_require_course_provenance_and_use_past_exams():
    course = Course(id=1, name='Métodos de Otimização e Análise de Dados', code='MOAD')
    files = [Material(id=1, course_id=1, course=course, title='Exame 2025', doc_kind='past_exam', terms={'simplex': 3}),
             Material(id=2, course_id=2, title='Exame de BD', doc_kind='past_exam', terms={'simplex': 3}),
             Material(id=3, course_id=1, course=course, title='Regras', doc_kind='admin', terms={'simplex': 3}),
             Material(id=4, course_id=1, course=course, title='Guia instalação', doc_kind='other', terms={'instalar': 3})]
    index = Index(files)
    assert [m['id'] for m in suggest_text(index, 'MOAD (Frequência 1)', 1, 'exam')] == [1]
    assert suggest_text(index, 'Teste 1', None, 'exam') == []
    assert suggest_text(Index([files[-1]]), 'MOAD (Frequência 1)', 1, 'exam') == []


def test_generic_academic_terms_do_not_make_a_document_relevant():
    index = Index([Material(id=1, title='Manual irrelevante', course_id=1, doc_kind='other',
                            terms={'grupo': 6, 'trabalho': 3, 'semana': 2})])
    assert suggest_text(index, 'Trabalho de grupo para esta semana', 1, 'assignment') == []


def test_existing_assessment_commitment_counts_as_preparation(monkeypatch):
    exam = Event(id=1, title='Avaliação', kind='exam', course_id=None, starts_at=NOW + timedelta(hours=20))
    target, = assessment_targets([exam], {}, PlanPrefs())
    planned = StudySession(id=1, event_id=1, event=exam, status='planned', starts_at=NOW + timedelta(hours=12),
                           ends_at=NOW + timedelta(hours=14))
    monkeypatch.setattr(academic, 'targets', lambda *args: [target])
    monkeypatch.setattr(academic.knowledge, 'load_index', lambda *args: object())
    db = Mock()
    db.scalars.side_effect = [[], [planned], [planned]]
    rec = academic.recommendation(db, NOW, PlanPrefs(), LX)
    assert rec['scheduled_minutes'] == 120
    assert rec['starts_at'] == planned.starts_at and not rec['warnings']


def test_moved_assessment_does_not_count_outdated_commitment_as_preparation(monkeypatch):
    exam = Event(id=1, title='Avaliação', kind='exam', course_id=None, starts_at=NOW + timedelta(hours=20))
    target, = assessment_targets([exam], {}, PlanPrefs())
    stale = StudySession(id=1, event_id=1, event=exam, status='planned', starts_at=NOW + timedelta(hours=22),
                         ends_at=NOW + timedelta(hours=24))
    assert academic.valid_allocation(stale, NOW) is False
    monkeypatch.setattr(academic, 'targets', lambda *args: [target])
    monkeypatch.setattr(academic.knowledge, 'load_index', lambda *args: object())
    db = Mock()
    db.scalars.side_effect = [[], [], [stale]]
    rec = academic.recommendation(db, NOW, PlanPrefs(), LX)
    assert rec['scheduled_minutes'] == 0 and rec['proposed_changes'][0]['session_id'] == 1


def test_nearly_finished_session_never_produces_a_zero_minute_recommendation(monkeypatch):
    exam = Event(id=1, title='Avaliação', kind='exam', course_id=None, starts_at=NOW + timedelta(hours=20))
    target, = assessment_targets([exam], {}, PlanPrefs())
    ending = StudySession(id=1, event_id=1, event=exam, status='planned', starts_at=NOW - timedelta(minutes=49),
                          ends_at=NOW + timedelta(seconds=30))
    monkeypatch.setattr(academic, 'targets', lambda *args: [target])
    monkeypatch.setattr(academic.knowledge, 'load_index', lambda *args: object())
    db = Mock()
    db.scalars.side_effect = [[], [ending], [ending]]
    rec = academic.recommendation(db, NOW, PlanPrefs(), LX)
    assert rec['minutes'] >= 15


def test_preparation_capacity_accounts_for_breaks(monkeypatch):
    now = NOW.replace(hour=9, minute=0)
    exam = Event(id=1, title='Avaliação', kind='exam', course_id=None, starts_at=now + timedelta(hours=2))
    monkeypatch.setattr(academic, 'targets', lambda *args: assessment_targets([exam], {}, PlanPrefs()))
    monkeypatch.setattr(academic.knowledge, 'load_index', lambda *args: object())
    db = Mock()
    db.scalars.side_effect = [[], [], []]
    rec = academic.recommendation(db, now, PlanPrefs(), LX)
    assert rec['available_minutes'] == 110 and rec['warnings']


@pytest.mark.parametrize('legacy', [False, True])
def test_completion_reopen_does_not_double_credit_or_change_unknown_historical_progress(monkeypatch, legacy):
    task = Task(id=1, title='Trabalho', progress=50 if legacy else 80, status='IN_PROGRESS', estimated_minutes=100)
    row = StudySession(id=1, task=task, status='done' if legacy else 'planned', progress_credit=-1 if legacy else 0,
                       starts_at=NOW, ends_at=NOW + timedelta(minutes=50))
    db = Mock()
    db.get.return_value = row
    for status, expected in [('done', 50 if legacy else 100), ('planned', 50 if legacy else 80), ('done', 50 if legacy else 100)]:
        routes.update_session(1, routes.s.SessionPatch(status=status), db)
        assert task.progress == expected
    assert bool(row.progress_note) == legacy


def test_stale_assessment_commitment_can_be_skipped():
    event = Event(id=1, title='Avaliação alterada', starts_at=NOW)
    row = StudySession(id=1, event=event, status='planned', starts_at=NOW + timedelta(hours=1),
                       ends_at=NOW + timedelta(hours=2))
    db = Mock()
    db.get.return_value = row
    routes.update_session(1, routes.s.SessionPatch(status='skipped'), db)
    assert row.status == 'skipped'


def test_assessment_migration_generates_reversible_postgresql_ddl():
    import importlib.util
    import io
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[1] / 'migrations/versions/0008_session_assessments.py'
    spec = importlib.util.spec_from_file_location('assessment_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
    with Operations.context(context):
        migration.upgrade()
    upgrade = output.getvalue()
    assert 'ADD COLUMN event_id' in upgrade and 'ON DELETE SET NULL' in upgrade
    assert "SET progress_credit = -1 WHERE status = 'done'" in upgrade
    assert 'DELETE FROM' not in upgrade and 'DROP TABLE' not in upgrade
    with Operations.context(context):
        migration.downgrade()
    assert 'DROP COLUMN event_id' in output.getvalue()


def test_automatic_download_enforces_actual_stream_size_and_removes_partial_file(monkeypatch, tmp_path):
    from peace import library
    monkeypatch.setenv('PEACE_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(library, 'MAX_BYTES', 4)
    material = Material(id=1, source='moodle', filename='a.txt', file_url='https://moodle.example.pt/file', size=None)
    monkeypatch.setattr(library, 'pending', lambda *args: [material])
    response, client = Mock(), Mock()
    response.iter_bytes.return_value = [b'123', b'456']
    monkeypatch.setattr(library.sync, 'open_moodle_file', lambda *args: (response, client))
    monkeypatch.setattr(library.sync, 'enabled_sources', lambda *args: ['moodle'])
    library.fetch_pending(Mock(), NOW)
    assert 'maior' in material.fetch_error
    assert not library.cached_file(1).exists() and not library.cached_file(1).with_suffix('.part').exists()
    response.close.assert_called_once()
    client.close.assert_called_once()


def test_advanced_assignment_deadline_does_not_count_later_commitments(monkeypatch):
    now = NOW.replace(hour=8, minute=0)
    task = Task(id=1, title='Entrega alterada', status='TODO', course_id=None, due_at=now + timedelta(hours=2),
                estimated_minutes=60, progress=0, priority=2, postponed_count=0)
    stale = StudySession(id=1, task=task, task_id=1, status='planned',
                         starts_at=now + timedelta(hours=5), ends_at=now + timedelta(hours=6))
    assert not academic.valid_allocation(stale, now)
    assert academic.valid_allocation(stale, task.due_at + timedelta(minutes=1))  # explicit overdue recovery
    monkeypatch.setattr(academic, 'targets', lambda *args: [task])
    monkeypatch.setattr(routes, '_prefs', lambda *args: PlanPrefs())
    monkeypatch.setattr(routes, '_tz', lambda: LX)
    kept, events, db = Mock(), Mock(), Mock()
    kept.all.return_value = [stale]
    events.all.return_value = []
    db.scalars.side_effect = [[stale], kept, events]
    routes._plan(db, [now.date()], now)
    created = db.add_all.call_args.args[0]
    assert created and all(s.ends_at <= task.due_at for s in created)
    assert stale.starts_at == now + timedelta(hours=5)  # proposal preserves the old commitment


def test_recommendation_capacity_respects_per_target_daily_limit(monkeypatch):
    now = NOW.replace(hour=9, minute=0)
    exam = Event(id=1, title='Avaliação', kind='exam', course_id=None, starts_at=now + timedelta(hours=8))
    prefs = PlanPrefs(max_task_minutes_per_day=30)
    monkeypatch.setattr(academic, 'targets', lambda *args: assessment_targets([exam], {}, prefs))
    monkeypatch.setattr(academic.knowledge, 'load_index', lambda *args: object())
    db = Mock()
    db.scalars.side_effect = [[], [], []]
    rec = academic.recommendation(db, now, prefs, LX)
    assert rec['available_minutes'] == 30 and rec['warnings']

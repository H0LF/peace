"""Database-backed acceptance for the fixed MOAD/BD scenario; requires a throwaway PostgreSQL."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from peace.api import app
from peace.models import Course, Event, StudySession, Task
from peace.notifications import briefing_text
from peace.routes import get_now


def test_moad_bd_recommendation_agrees_across_api_briefing_and_plan(client, session):
    now = datetime(2026, 10, 26, 21, 6, tzinfo=UTC)
    app.dependency_overrides[get_now] = lambda: now
    try:
        moad, bd = Course(name='MOAD'), Course(name='BD')
        session.add_all([moad, bd])
        session.flush()
        first = Event(title='MOAD (Frequência 1)', kind='exam', course_id=moad.id,
                      starts_at=datetime(2026, 10, 27, 17, tzinfo=UTC), ends_at=datetime(2026, 10, 27, 19, tzinfo=UTC))
        second = Event(title='BD (Teste 1)', kind='exam', course_id=bd.id,
                       starts_at=datetime(2026, 10, 28, 16, tzinfo=UTC), ends_at=datetime(2026, 10, 28, 18, tzinfo=UTC))
        meta = Task(title='Meta3 — Modelos ER', course_id=bd.id, due_at=now + timedelta(days=7))
        session.add_all([first, second, meta])
        session.commit()
        rec = client.get('/api/v1/recommendation').json()
        assert rec['event_id'] == first.id and rec['task_id'] is None
        assert client.get('/api/v1/today').json()['recommendation'] == rec
        assert rec['text'] in briefing_text(session, now, ZoneInfo('Europe/Lisbon'))
        plan = client.post('/api/v1/plan/week').json()
        assert {s['event']['id'] for s in plan if s['event']} == {first.id, second.id}
        count = session.query(StudySession).count()
        client.post('/api/v1/plan/week')
        assert session.query(StudySession).count() == count
    finally:
        app.dependency_overrides.pop(get_now, None)

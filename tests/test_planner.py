from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from peace.planner import PlanPrefs, assessment_targets, free_slots, plan_day, ranked, recommend, score, window

LX = ZoneInfo("Europe/Lisbon")
PREFS = PlanPrefs()
MONDAY = date(2026, 10, 26)
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)  # 08:00 in Lisbon (WET, UTC+0)


def task(id, **kw):
    base = dict(id=id, title=f"T{id}", course_id=None, priority=2, due_at=None,
                estimated_minutes=60, progress=0, postponed_count=0)
    return SimpleNamespace(**(base | kw))


def utc(h, m=0, day=26):
    return datetime(2026, 10, day, h, m, tzinfo=UTC)


def test_overdue_then_due_soon_then_no_deadline():
    tasks = [task(1), task(2, due_at=NOW + timedelta(hours=30)), task(3, due_at=NOW - timedelta(hours=1))]
    assert [t.id for t, _, _ in ranked(tasks, NOW, PREFS)] == [3, 2, 1]


def test_work_due_within_four_hours_outranks_overdue_work():
    overdue = task(1, due_at=NOW - timedelta(hours=5), postponed_count=1)
    imminent = task(2, due_at=NOW + timedelta(hours=3, minutes=59), estimated_minutes=10)
    later = task(3, due_at=NOW + timedelta(hours=4))
    assert [t.id for t, _, _ in ranked([overdue, imminent, later], NOW, PREFS)] == [2, 1, 3]
    assert 'prazo iminente' in score(imminent, NOW, PREFS)[1]
    # Explicitly critical overdue work still overrides the normal ranking.
    assert recommend([imminent, task(1, priority=4, due_at=NOW - timedelta(hours=5))], NOW, PREFS)['task_id'] == 1


def test_imminent_submission_outranks_overdue_work_and_tomorrows_exam():
    targets = assessment_targets([exam(9, 'Avaliação', NOW + timedelta(hours=20))], {}, PREFS)
    overdue = task(1, due_at=NOW - timedelta(hours=1))
    imminent = task(2, due_at=NOW + timedelta(hours=2))
    order = [getattr(t, 'event_id', None) or t.id for t, _, _ in ranked([overdue, *targets, imminent], NOW, PREFS)]
    assert order == [2, 9, 1]


def test_equal_scores_rank_by_earlier_deadline_then_id():
    tasks = [task(3, due_at=NOW + timedelta(days=9)), task(2, due_at=NOW + timedelta(days=8)), task(1, due_at=NOW + timedelta(days=9))]
    assert len({s for _, s, _ in ranked(tasks, NOW, PREFS)}) == 1
    assert [t.id for t, _, _ in ranked(tasks, NOW, PREFS)] == [2, 1, 3]


def test_reasons_are_explainable_in_portuguese():
    t = task(1, priority=3, due_at=NOW + timedelta(hours=50), postponed_count=1)
    _, reasons = score(t, NOW, PREFS)
    assert reasons == ["entrega em 2 dias", "prioridade alta", "adiada 1 vez"]


def test_days_until_deadline_round_to_nearest_day():
    assert score(task(1, due_at=NOW + timedelta(hours=47, minutes=59)), NOW, PREFS)[1][0] == "entrega em 2 dias"
    assert score(task(1, due_at=NOW + timedelta(hours=26)), NOW, PREFS)[1][0] == "entrega em 1 dia"


def test_heavy_remaining_work_raises_score():
    light = task(1, due_at=NOW + timedelta(days=10), estimated_minutes=60)
    heavy = task(2, due_at=NOW + timedelta(days=10), estimated_minutes=1800)
    assert score(heavy, NOW, PREFS)[0] > score(light, NOW, PREFS)[0]
    assert "muito trabalho para o tempo que resta" in score(heavy, NOW, PREFS)[1]


def test_progress_reduces_remaining_and_finished_work_is_not_ranked():
    assert ranked([task(1, progress=100)], NOW, PREFS) == []


@pytest.mark.parametrize(
    ("day", "utc_hour"),
    [
        (date(2026, 3, 28), 9),   # WET, before spring-forward
        (date(2026, 3, 29), 8),   # WEST from 29 March
        (date(2026, 10, 24), 8),  # WEST, before fall-back
        (date(2026, 10, 25), 9),  # WET from 25 October
    ],
)
def test_study_window_follows_lisbon_dst(day, utc_hour):
    start, end = window(day, LX, PREFS)
    assert (start.hour, start.astimezone(LX).time()) == (utc_hour, time(9))
    assert end.astimezone(LX).time() == time(21)


def test_free_slots_skip_classes_and_time_already_past():
    now = utc(10, 2)
    slots = free_slots(MONDAY, LX, PREFS, [(utc(11), utc(13))], now)
    assert slots == [(utc(10, 5), utc(11)), (utc(13), utc(21))]


def test_plan_day_uses_blocks_breaks_task_cap_and_daily_budget():
    tasks = [task(1, estimated_minutes=600, priority=4), task(2, estimated_minutes=600), task(3, estimated_minutes=600)]
    plan = plan_day(MONDAY, LX, PREFS, tasks, [], NOW)
    assert [(p.task_id, p.starts_at, p.ends_at) for p in plan[:3]] == [
        (1, utc(9), utc(9, 50)), (1, utc(10), utc(10, 50)), (1, utc(11), utc(11, 20)),
    ]
    per_task = {i: sum((p.ends_at - p.starts_at).seconds // 60 for p in plan if p.task_id == i) for i in (1, 2, 3)}
    assert per_task == {1: 120, 2: 120, 3: 0}  # 240-minute daily budget


def test_plan_day_never_overlaps_busy_time():
    busy = [(utc(9, 30), utc(10, 30)), (utc(12), utc(14))]
    plan = plan_day(MONDAY, LX, PREFS, [task(1, estimated_minutes=600), task(2, estimated_minutes=600)], busy, NOW)
    for p in plan:
        assert all(p.ends_at <= b0 or p.starts_at >= b1 for b0, b1 in busy)


def test_plan_day_stops_at_the_deadline():
    plan = plan_day(MONDAY, LX, PREFS, [task(1, estimated_minutes=120, due_at=utc(10))], [], NOW)
    assert [(p.starts_at, p.ends_at) for p in plan] == [(utc(9), utc(9, 50))]


def test_no_work_planned_after_a_future_deadline_has_passed():
    tasks = [task(1, due_at=utc(12))]
    assert plan_day(MONDAY + timedelta(days=1), LX, PREFS, tasks, [], NOW) == []


def test_allocated_minutes_on_other_days_are_not_planned_twice():
    one = [task(1, estimated_minutes=60)]
    assert [(p.starts_at, p.ends_at) for p in plan_day(MONDAY, LX, PREFS, one, [], NOW, allocated={1: 30})] == [
        (utc(9), utc(9, 30))
    ]
    assert plan_day(MONDAY, LX, PREFS, one, [], NOW, allocated={1: 50}) == []  # 10 min left < one block


def test_recommendation_text():
    rec = recommend([task(1, due_at=NOW - timedelta(hours=2))], NOW, PREFS)
    assert rec["text"] == "Recomendado: 50 minutos em «T1» (em atraso)."
    assert recommend([], NOW, PREFS) is None


def test_prefs_reject_inverted_window():
    with pytest.raises(ValidationError):
        PlanPrefs(study_start=time(20), study_end=time(8))


def exam(id, title, due):
    return SimpleNamespace(id=id, title=title, course_id=id, starts_at=due)


def test_moad_bd_october_26_regression():
    now = utc(21, 6)
    exams = assessment_targets([exam(1, 'MOAD (Frequência 1)', utc(17, day=27)),
                               exam(2, 'BD (Teste 1)', utc(16, day=28))], {}, PREFS)
    meta = task(3, title='Meta3 — Modelos ER', due_at=utc(0, day=31))
    targets = [meta, *exams]
    rec = recommend(targets, now, PREFS)
    assert rec['event_id'] == 1 and rec['task_id'] is None
    assert 'avaliação' in rec['text'] and rec['readiness'] is None and rec['assessment_weight'] is None
    plan = plan_day(MONDAY + timedelta(days=1), LX, PREFS, targets, [], now)
    assert [p.event_id for p in plan if p.event_id][:1] == [1]
    assert {p.event_id for p in plan if p.event_id} == {1, 2}


@pytest.mark.parametrize('hours', [8, 23, 48, 72])
def test_exam_beats_assignment_next_week(hours):
    targets = assessment_targets([exam(1, 'Avaliação', NOW + timedelta(hours=hours))], {}, PREFS)
    rec = recommend([task(1, kind='assignment', due_at=NOW + timedelta(days=7)), *targets], NOW, PREFS)
    assert rec['event_id'] == 1


def test_critical_user_priority_and_urgent_submission_can_outrank_exam():
    targets = assessment_targets([exam(1, 'Avaliação', NOW + timedelta(hours=23))], {}, PREFS)
    for urgent in [task(2, priority=4), task(2, due_at=NOW + timedelta(hours=2)), task(2, due_at=NOW - timedelta(hours=1))]:
        assert recommend([*targets, urgent], NOW, PREFS)['task_id'] == 2


def test_completed_exam_preparation_is_not_repeated():
    targets = assessment_targets([exam(1, 'Avaliação', utc(17, day=27))], {1: 120}, PREFS)
    assert recommend(targets, NOW, PREFS) is None


def test_same_day_exams_share_scarce_time_without_conflicts():
    targets = assessment_targets([exam(1, 'Primeira', utc(14)), exam(2, 'Segunda', utc(16))], {}, PREFS)
    prefs = PlanPrefs(max_daily_minutes=60)
    plan = plan_day(MONDAY, LX, prefs, targets, [(utc(9), utc(10))], NOW)
    assert {p.event_id for p in plan} == {1, 2}
    assert sum((p.ends_at - p.starts_at).total_seconds() / 60 for p in plan) == 60
    assert all(p.starts_at >= utc(10) and p.ends_at <= utc(14) for p in plan)


def test_midnight_and_timezone_offsets_keep_same_assessment_priority():
    now = datetime(2026, 10, 26, 23, 59, tzinfo=LX)
    target = assessment_targets([exam(1, 'Avaliação', utc(0, 30, day=27))], {}, PREFS)
    other = task(2, due_at=utc(12, day=31))
    assert recommend([other, *target], now, PREFS)['event_id'] == 1
    assert recommend([other, *target], now.astimezone(ZoneInfo('America/New_York')), PREFS)['event_id'] == 1


def test_expired_assessments_and_finished_or_blocked_tasks_are_excluded():
    targets = assessment_targets([exam(1, 'Avaliação', NOW - timedelta(hours=1))], {}, PREFS)
    assert recommend([*targets, task(2, status='COMPLETED'), task(3, status='BLOCKED')], NOW, PREFS) is None


def test_distant_exams_do_not_dilute_tomorrows_preparation():
    exams = [exam(1, 'Amanhã', NOW + timedelta(hours=32)),
             *[exam(i, 'Distante', NOW + timedelta(days=20)) for i in range(2, 15)]]
    plan = plan_day(MONDAY, LX, PREFS, assessment_targets(exams, {}, PREFS), [], NOW)
    assert sum((p.ends_at - p.starts_at).total_seconds() / 60 for p in plan if p.event_id == 1) == 120

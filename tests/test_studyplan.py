"""Study plans: a realistic schedule, honest about its evidence, that adapts when things change."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from peace import assistant, routes, studyplan
from peace.api import app
from peace.models import GRADE_KINDS, Concept, ConceptAnswer, Course, Event, Material, Preference, StudyGuide, StudyPlan, StudySession, Task
from peace.planner import PlanPrefs
from peace.studyplan import schedule

TZ = ZoneInfo("Europe/Lisbon")
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=TZ).astimezone(UTC)  # a Monday
EXAM = datetime(2026, 11, 9, 10, 0, tzinfo=TZ).astimezone(UTC)  # next Monday
PREFS = PlanPrefs()
GRADE = GRADE_KINDS[0]


def minutes(rows, activity=None):
    return sum(int((r.ends_at - r.starts_at).total_seconds() // 60) for r in rows if activity in (None, r.activity))


def topics(*spec):
    return [{"topic": t, "minutes": m} for t, m in spec]


# --- the schedule -------------------------------------------------------------------------------------------------


def test_with_time_to_spare_everything_fits_in_a_sensible_order():
    placed, total = schedule(topics(("Normalização", 120), ("SQL", 100)), EXAM, NOW, TZ, PREFS)
    assert minutes(placed, "learn") == 220 and minutes(placed) == total
    assert all(p.starts_at.astimezone(TZ).time() >= time(9) and p.ends_at.astimezone(TZ).time() <= time(21) for p in placed)
    assert all(p.ends_at <= EXAM for p in placed)
    order = [p.activity for p in sorted(placed, key=lambda p: p.starts_at)]
    assert order[-1] == "review" and placed[-1].starts_at.astimezone(TZ).date() == date(2026, 11, 8)  # the day before
    first = {p.topic: p.starts_at for p in sorted(placed, key=lambda p: -p.starts_at.timestamp()) if p.activity == "learn"}
    assert first["Normalização"] < first["SQL"]  # most important first
    for t in ("Normalização", "SQL"):  # a recall the day after the topic is finished
        finished = max(p.ends_at for p in placed if p.activity == "learn" and p.topic == t)
        recall = next(p for p in placed if p.activity == "recall" and p.topic == t)
        assert recall.starts_at.astimezone(TZ).date() > finished.astimezone(TZ).date()
    assert minutes(placed, "practice") == round(220 * studyplan.PRACTICE_SHARE)


def test_no_session_overlaps_breaks_are_kept_and_the_daily_limit_holds():
    placed, _ = schedule(topics(*[(f"T{i}", 200) for i in range(5)]), EXAM, NOW, TZ, PREFS)
    ordered = sorted(placed, key=lambda p: p.starts_at)
    for a, b in zip(ordered, ordered[1:]):
        assert b.starts_at >= a.ends_at + timedelta(minutes=PREFS.break_minutes)
    per_day: dict[date, int] = {}
    for p in placed:
        day = p.starts_at.astimezone(TZ).date()
        per_day[day] = per_day.get(day, 0) + minutes([p])
    assert max(per_day.values()) <= PREFS.max_daily_minutes


def test_too_little_time_keeps_the_most_important_and_says_how_much_is_missing():
    tomorrow = NOW + timedelta(days=1)
    placed, total = schedule(topics(("Primeiro", 200), ("Segundo", 200), ("Terceiro", 800)), tomorrow, NOW, TZ, PREFS)
    learned = {p.topic for p in placed if p.activity == "learn"}
    assert "Primeiro" in learned and "Terceiro" not in learned
    assert minutes(placed) < total and minutes(placed) <= PREFS.max_daily_minutes


def test_classes_and_earlier_commitments_are_worked_around():
    day = date(2026, 11, 3)
    class_start = datetime.combine(day, time(9), TZ).astimezone(UTC)
    class_end = datetime.combine(day, time(19), TZ).astimezone(UTC)
    placed, _ = schedule(topics(("A", 600)), EXAM, NOW, TZ, PREFS, busy=lambda d: [(class_start, class_end)] if d == day else [])
    assert not [p for p in placed if p.starts_at < class_end and p.ends_at > class_start]


def test_exam_today_only_uses_the_time_before_it():
    exam = datetime(2026, 11, 2, 12, 0, tzinfo=TZ).astimezone(UTC)
    placed, _ = schedule(topics(("A", 300)), exam, NOW, TZ, PREFS)
    assert placed and all(p.ends_at <= exam for p in placed)


def test_an_exam_with_no_free_time_gives_an_empty_plan_not_a_crash():
    prefs = PlanPrefs(max_daily_minutes=15, block_minutes=15)
    exam = NOW + timedelta(minutes=10)
    assert schedule(topics(("A", 100)), exam, NOW, TZ, prefs)[0] == []


def test_a_sunday_exam_across_a_clock_change_stays_in_local_hours():
    now = datetime(2026, 10, 23, 8, 0, tzinfo=TZ).astimezone(UTC)
    exam = datetime(2026, 10, 26, 10, 0, tzinfo=TZ).astimezone(UTC)  # the clocks change on the 25th
    placed, _ = schedule(topics(("A", 300)), exam, now, TZ, PREFS)
    assert all(9 <= p.starts_at.astimezone(TZ).hour < 21 for p in placed)


# --- the plan -----------------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(assistant, "llm_reachable", lambda: False)  # rules only: never whatever model this computer runs
    app.dependency_overrides[routes.get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(routes.get_now, None)


@pytest.fixture
def exam(session):
    course = Course(name="Bases de Dados", code="BD")
    session.add(course)
    session.flush()
    row = Event(title="Exame de BD", kind="exam", course_id=course.id, starts_at=EXAM, ends_at=EXAM + timedelta(hours=2))
    session.add(row)
    session.commit()
    return row


def make(client, exam):
    r = client.post("/api/v1/study-plans", json={"event_id": exam.id})
    assert r.status_code == 201, r.text
    return r.json()


def test_without_materials_it_uses_a_stated_default_and_says_so(client, exam):
    plan = make(client, exam)
    assert plan["basis"] == "none" and plan["sessions"]
    assert any("valor por defeito" in a for a in plan["assumptions"])
    assert any("Não encontrei materiais" in a for a in plan["assumptions"])


def test_the_guide_is_the_best_evidence_and_its_files_reach_the_sessions(client, exam, session):
    session.add(StudyGuide(event_id=exam.id, minutes=200, signature="x", topics=[
        {"topic": "Normalização", "minutes": 120, "files": [{"id": 7, "name": "aula3.pdf", "vault_path": "BD/aula3.pdf"}]},
        {"topic": "SQL", "minutes": 80, "files": []}]))
    session.commit()
    plan = make(client, exam)
    assert plan["basis"] == "guide" and any("estimativa" in a for a in plan["assumptions"])
    assert [t["topic"] for t in plan["topics"]] == ["Normalização", "SQL"]
    learn = next(s for s in plan["sessions"] if s["activity"] == "learn" and s["topic"] == "Normalização")
    assert learn["materials"][0]["filename"] == "aula3.pdf" and learn["event"]["id"] == exam.id
    assert {s["activity"] for s in plan["sessions"]} == {"learn", "recall", "practice", "review"}
    assert plan["enough_time"] is True and plan["short_minutes"] == 0


def test_weak_topics_come_first_with_more_time_strong_ones_with_less(client, exam, session):
    weak = Concept(course_id=exam.course_id, name="Normalização", description="")
    strong = Concept(course_id=exam.course_id, name="SQL", description="")
    other = Concept(course_id=exam.course_id, name="Transações", description="")
    session.add_all([strong, other, weak])
    session.flush()
    for i in range(4):
        session.add(ConceptAnswer(concept_id=weak.id, score=0.0, grade=GRADE, source="test", answered_at=NOW - timedelta(days=1)))
    for i in range(20):
        session.add(ConceptAnswer(concept_id=strong.id, score=1.0, grade=GRADE, source="test", confirmed=True, answered_at=NOW - timedelta(days=i % 5 + 1)))
    session.commit()
    plan = make(client, exam)
    by_topic = {t["topic"]: t for t in plan["topics"]}
    assert plan["basis"] == "concepts" and plan["topics"][0]["topic"] == "Normalização"
    assert by_topic["Normalização"]["minutes"] == 90 and by_topic["SQL"]["minutes"] == 30 and by_topic["Transações"]["minutes"] == 60


def test_file_topics_are_used_and_admin_files_are_not(client, exam, session):
    session.add_all([
        Material(course_id=exam.course_id, section="s", title="Aula 1", kind="file", doc_kind="slides", topics=["Modelo ER"], external_id="a"),
        Material(course_id=exam.course_id, section="s", title="Regulamento", kind="file", doc_kind="admin", topics=["Assiduidade"], external_id="b"),
    ])
    session.commit()
    plan = make(client, exam)
    assert plan["basis"] == "files" and [t["topic"] for t in plan["topics"]] == ["Modelo ER"]


def test_making_a_plan_twice_does_not_duplicate_and_the_general_planner_stays_out(client, exam, session):
    session.add(StudySession(event_id=exam.id, course_id=exam.course_id, starts_at=NOW + timedelta(hours=26), ends_at=NOW + timedelta(hours=27)))
    session.commit()
    first = make(client, exam)
    second = make(client, exam)
    assert first["id"] == second["id"] and len(first["sessions"]) == len(second["sessions"])
    assert not [s for s in second["sessions"] if s["plan_id"] is None]
    before = session.query(StudySession).count()
    assert client.post("/api/v1/plan/week").status_code == 200
    session.expire_all()
    assert session.query(StudySession).count() == before  # no second set of generic sessions for this exam


def test_only_future_exams_can_have_a_plan(client, session):
    past = Event(title="Exame antigo", kind="exam", starts_at=NOW - timedelta(days=1), ends_at=NOW - timedelta(hours=22))
    other = Event(title="Aula", kind="class", starts_at=NOW + timedelta(days=1), ends_at=NOW + timedelta(days=1, hours=1))
    session.add_all([past, other])
    session.commit()
    assert client.post("/api/v1/study-plans", json={"event_id": past.id}).status_code == 422
    assert client.post("/api/v1/study-plans", json={"event_id": other.id}).status_code == 404


def test_listing_offers_exams_without_a_plan_and_shows_progress(client, exam, session):
    listing = client.get("/api/v1/study-plans").json()
    assert listing["plans"] == [] and listing["exams"][0]["id"] == exam.id
    plan = make(client, exam)
    listing = client.get("/api/v1/study-plans").json()
    assert listing["exams"] == [] and listing["plans"][0]["id"] == plan["id"] and listing["plans"][0]["progress"] == 0
    s = plan["sessions"][0]
    assert client.patch(f"/api/v1/sessions/{s['id']}", json={"status": "done"}).status_code == 200
    assert client.get("/api/v1/study-plans").json()["plans"][0]["progress"] > 0


def test_replanning_keeps_what_was_done_and_replaces_what_was_missed(client, exam, session):
    session.add(StudyGuide(event_id=exam.id, minutes=300, signature="x", topics=[{"topic": "Normalização", "minutes": 300, "files": []}]))
    session.commit()
    plan = make(client, exam)
    first, second = [s for s in plan["sessions"] if s["activity"] == "learn"][:2]
    client.patch(f"/api/v1/sessions/{first['id']}", json={"status": "done"})
    later = datetime.fromisoformat(second["ends_at"]) + timedelta(hours=1)
    app.dependency_overrides[routes.get_now] = lambda: later
    again = client.post(f"/api/v1/study-plans/{plan['id']}/replan").json()
    session.expire_all()
    assert session.get(StudySession, first["id"]).status == "done"
    assert session.get(StudySession, second["id"]).status == "skipped"
    assert all(datetime.fromisoformat(s["starts_at"]) >= later for s in again["sessions"])
    done = minutes([session.get(StudySession, first["id"])])
    assert minutes([type("R", (), {"starts_at": datetime.fromisoformat(s["starts_at"]), "ends_at": datetime.fromisoformat(s["ends_at"]), "activity": s["activity"]}) for s in again["sessions"]], "learn") == 300 - done


def test_the_worker_replans_when_the_exam_moves_or_a_class_lands_on_a_session(client, exam, session):
    plan = make(client, exam)
    assert studyplan.adapt_all(session, NOW) == 0  # nothing changed: nothing rewritten
    moved = EXAM - timedelta(days=2)
    session.get(Event, exam.id).starts_at = moved
    session.get(Event, exam.id).ends_at = moved + timedelta(hours=2)
    session.commit()
    assert studyplan.adapt_all(session, NOW) == 1
    session.commit()
    rows = session.query(StudySession).filter(StudySession.plan_id == plan["id"], StudySession.status == "planned").all()
    assert rows and all(r.ends_at <= moved for r in rows)
    assert studyplan.adapt_all(session, NOW) == 0
    r = rows[0]
    session.add(Event(title="Aula nova", kind="class", starts_at=r.starts_at, ends_at=r.ends_at))
    session.commit()
    assert studyplan.adapt_all(session, NOW) == 1


def test_the_worker_waits_a_day_before_calling_a_session_missed(client, exam, session):
    plan = make(client, exam)
    first = datetime.fromisoformat(plan["sessions"][0]["ends_at"])
    assert studyplan.adapt_all(session, first + timedelta(minutes=5)) == 0  # could still be marked done today
    next_day = datetime.combine(first.astimezone(TZ).date() + timedelta(days=1), time(8), TZ).astimezone(UTC)
    assert studyplan.adapt_all(session, next_day) == 1
    session.commit()
    session.expire_all()
    assert session.get(StudySession, plan["sessions"][0]["id"]).status == "skipped"


def test_a_plan_made_before_the_guide_exists_improves_when_it_arrives(client, exam, session):
    make(client, exam)
    session.add(StudyGuide(event_id=exam.id, minutes=60, signature="x", topics=[{"topic": "SQL", "minutes": 60, "files": []}]))
    session.commit()
    assert studyplan.adapt_all(session, NOW) == 1
    session.commit()
    session.expire_all()
    assert session.query(StudyPlan).one().basis == "guide"


def test_work_due_before_the_exam_keeps_its_time(client, exam, session):
    session.add(Task(title="Relatório", kind="assignment", due_at=EXAM - timedelta(days=1), estimated_minutes=600, status="TODO"))
    session.commit()
    plan = make(client, exam)
    assert any("1 entrega" in a for a in plan["assumptions"])


def test_deleting_a_plan_keeps_the_record_of_what_was_studied(client, exam, session):
    plan = make(client, exam)
    done, gone = plan["sessions"][0]["id"], plan["sessions"][1]["id"]
    client.patch(f"/api/v1/sessions/{done}", json={"status": "done"})
    assert client.delete(f"/api/v1/study-plans/{plan['id']}").status_code == 204
    session.expire_all()
    assert session.get(StudySession, done).status == "done" and session.get(StudySession, gone) is None


# --- in words -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text,action", [
    ("Peace, create a study plan for my Databases exam.", "create"),
    ("cria um plano de estudo para o exame de BD", "create"),
    ("I have an exam next Friday. Help me prepare.", "create"),
    ("tenho um exame na sexta, ajuda-me a preparar", "create"),
    ("Reorganize my study plan because I missed yesterday.", "replan"),
    ("perdi a sessão de ontem", "replan"),
    ("What should I study today?", "today"),
    ("o que devo estudar hoje?", "today"),
    ("Which course documents should I study today?", "today"),
    ("Do I have enough time to prepare for this exam?", "enough"),
    ("tenho tempo suficiente para o exame de BD?", "enough"),
])
def test_requests_for_a_plan_are_recognised_in_both_languages(text, action):
    assert studyplan.parse(text) == action


@pytest.mark.parametrize("text", ["planear a semana", "o que tenho hoje?", "tenho exame amanhã?", "marca uma reunião às 15h", "plano da semana"])
def test_other_requests_are_left_alone(text):
    assert studyplan.parse(text) is None


def ask(client, text):
    return client.post("/api/v1/assistant/ask", json={"text": text}).json()


def test_asking_creates_the_plan_and_reports_what_was_placed(client, exam, session):
    r = ask(client, "cria um plano de estudo para o exame de BD")
    assert r["intent"] == "study_plan" and "Criei o plano para Exame de BD" in r["reply"] and r["to"] == "/estudo"
    session.expire_all()
    assert session.query(StudyPlan).count() == 1


def test_english_finds_the_course_by_name(client, exam):
    assert "Criei o plano" in ask(client, "create a study plan for my BD exam")["reply"]


def test_a_new_exam_is_added_when_the_student_says_course_and_day(client, session):
    session.add(Course(name="Redes de Computadores", code="RC"))
    session.commit()
    assert "Criei o plano" in ask(client, "tenho exame de RC na sexta, ajuda-me a preparar")["reply"]
    session.expire_all()
    assert session.query(Event).filter(Event.kind == "exam").one().starts_at > NOW


def test_two_exams_and_no_course_named_asks_which(client, exam, session):
    session.add(Event(title="Teste de Redes", kind="exam", starts_at=EXAM + timedelta(days=2), ends_at=EXAM + timedelta(days=2, hours=1)))
    session.commit()
    r = ask(client, "cria um plano de estudo")["reply"]
    assert "Para qual" in r and "Exame de BD" in r and "Teste de Redes" in r
    session.expire_all()
    assert session.query(StudyPlan).count() == 0


def test_the_next_exam_needs_no_name(client, exam, session):
    session.add(Event(title="Teste de Redes", kind="exam", starts_at=EXAM + timedelta(days=2), ends_at=EXAM + timedelta(days=2, hours=1)))
    session.commit()
    assert "Exame de BD" in ask(client, "cria um plano de estudo para o próximo exame")["reply"]


def test_nothing_known_asks_instead_of_inventing(client):
    assert "Não encontrei essa avaliação" in ask(client, "cria um plano de estudo")["reply"]


def test_enough_time_tries_it_without_keeping_a_plan(client, exam, session):
    r = ask(client, "tenho tempo suficiente para o exame de BD?")["reply"]
    assert "tempo suficiente" in r and "Ainda não criei" in r
    session.expire_all()
    assert session.query(StudyPlan).count() == 0 and session.query(StudySession).count() == 0


def test_today_follows_the_plan_and_falls_back_without_one(client, exam):
    assert ask(client, "o que devo estudar hoje?")["intent"] == "study_now"
    make(client, exam)
    r = ask(client, "what should I study today?")
    assert r["intent"] == "study_plan" and "A seguir" in r["reply"]


def test_replan_in_words_without_a_plan_explains(client, exam):
    assert "Ainda não tens nenhum plano" in ask(client, "reorganize my study plan")["reply"]
    make(client, exam)
    assert "Reorganizei" in ask(client, "perdi a sessão de ontem")["reply"]


def test_prefs_shape_the_plan(client, exam, session):
    session.add(Preference(key="planner", value=PlanPrefs(max_daily_minutes=60, block_minutes=30).model_dump(mode="json")))
    session.commit()
    plan = make(client, exam)
    assert any("Até 60 min" in a for a in plan["assumptions"])

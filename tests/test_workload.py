"""Deliveries read from a brief, and study guides for exams, with a stand-in for the model."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from peace import workload
from peace.api import app
from peace.models import Course, Event, Material, StudyGuide, Task
from peace.planner import PlanPrefs, assessment_targets
from peace.requirements import ModelUnavailable
from peace.routes import get_now

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
TZ = ZoneInfo("Europe/Lisbon")
API = "/api/v1"
BRIEF = ("[p. 1]\nO programa deve simular uma fábrica.\n[p. 8]\nMetas e entregas\n"
         "Checkpoint 1 → 8 de novembro, 12h00\nCheckpoint 2 → 22 de novembro, 12h00\nEntrega final → 13 de dezembro, 12h00\n")


def answers(*replies):
    queue = list(replies)
    return lambda *_a, **_k: queue.pop(0)


def brief(session, content=BRIEF, filename="Enunciado do trabalho prático.pdf", doc_kind="statement"):
    course = session.query(Course).filter_by(name="POO").one_or_none() or Course(name="POO", code="1")
    session.add(course)
    session.flush()
    m = Material(course_id=course.id, section="P", title=filename, kind="file", filename=filename, content=content,
                 doc_kind=doc_kind, topics=["classes"], topics_at=NOW, fetched_at=NOW - timedelta(days=1),
                 modified_at=datetime(2026, 10, 20, tzinfo=UTC), source="inforestudante", external_id=f"1:2:{filename}")
    session.add(m)
    session.commit()
    return m


def delivery(title, when, quote):
    return {"title": title, "when": when, "quote": quote}


READ = {"deliveries": [delivery("Checkpoint 1", "8 de novembro, 12h00", "Checkpoint 1 → 8 de novembro, 12h00"),
                       delivery("Entrega final", "13 de dezembro, 12h00", "Entrega final → 13 de dezembro, 12h00"),
                       delivery("Inventada", "1 de dezembro", "A entrega é a 1 de dezembro."),  # not in the file
                       delivery("Sem data no texto", "20 de novembro", "Checkpoint 1 → 8 de novembro, 12h00"),
                       delivery("Passada", "1 de outubro", "Metas e entregas")],
        "work": ["Simular uma fábrica"]}
HOURS = {"deliveries": [{"n": 1, "what": ["Criar a fábrica", "Fazer o relatório"], "hours": 12, "why": "É o início."},
                        {"n": 2, "what": ["Terminar tudo"], "hours": 9000, "why": "x"}]}


def test_brief_becomes_tasks_with_quoted_dates_and_estimates(session, monkeypatch):
    m = brief(session)
    monkeypatch.setattr(workload, "ask_json", answers(READ, READ, HOURS))
    workload.read_brief(session, m, NOW)
    session.commit()
    first, final = session.query(Task).order_by(Task.due_at).all()
    assert (first.title, first.due_at.astimezone(TZ).hour, first.due_at.astimezone(TZ).day) == ("Checkpoint 1", 12, 8)
    assert (first.kind, first.course_id, first.brief_material_id, first.estimated_minutes) == ("assignment", m.course_id, m.id, 720)
    assert "- Criar a fábrica" in first.description and "p. 8" in first.description and "~12 h" in first.description
    assert final.estimated_minutes == 300 * 60  # the model's number is kept within sense
    assert m.briefs_at == NOW


def test_a_quote_cut_by_bullets_and_the_weekday_next_to_the_date_still_count(session, monkeypatch):
    m = brief(session, content="Calendário\n\uf0b7 Meta final\n→ 12 de dezembro (sábado), 23h30\n")
    monkeypatch.setattr(workload, "ask_json", answers(
        {"deliveries": [delivery("Meta final", "12 de dezembro, 23h30", "Meta final → 12 de dezembro (sábado), 23h30")]},
        {"work": []}, {"deliveries": []}))
    workload.read_brief(session, m, NOW)
    session.commit()
    task = session.query(Task).one()
    assert task.due_at.astimezone(TZ).day == 12 and task.due_at.astimezone(TZ).hour == 23


def test_reading_again_keeps_the_students_changes_and_duplicates_nothing(session, monkeypatch):
    m = brief(session)
    monkeypatch.setattr(workload, "ask_json", answers(READ, READ, HOURS, READ, READ, HOURS))
    workload.read_brief(session, m, NOW)
    session.commit()
    task = session.query(Task).order_by(Task.due_at).first()
    task.status, task.progress = "IN_PROGRESS", 40
    session.commit()
    workload.read_brief(session, m, NOW)
    session.commit()
    session.refresh(task)
    assert session.query(Task).count() == 2 and (task.status, task.progress) == ("IN_PROGRESS", 40)


def test_a_delivery_the_site_already_has_only_gets_the_briefs_detail(session, monkeypatch):
    m = brief(session)
    site = Task(title="Submissão checkpoint", kind="assignment", course_id=m.course_id, source="moodle", external_id="assign:1",
                due_at=datetime(2026, 11, 8, 20, 0, tzinfo=UTC), description="")
    signup = Task(title="Inscrição para defesa", kind="assignment", course_id=m.course_id, source="moodle", external_id="assign:2",
                  due_at=datetime(2026, 12, 13, 9, 0, tzinfo=UTC), description="")
    session.add_all([site, signup])
    session.commit()
    monkeypatch.setattr(workload, "ask_json", answers(READ, READ, HOURS))
    workload.read_brief(session, m, NOW)
    session.commit()
    session.refresh(site), session.refresh(signup)
    assert session.query(Task).count() == 3  # Checkpoint 1 merged; the final delivery is not the sign-up
    assert (site.title, site.estimated_minutes, site.brief_material_id) == ("Submissão checkpoint", 720, m.id)
    assert "- Criar a fábrica" in site.description
    assert signup.estimated_minutes is None


def test_every_date_reaches_the_model_and_a_file_without_dates_costs_no_call(session, monkeypatch):
    long = "palavras sem datas. " * 2000
    excerpts = workload.date_excerpts(f"{long}Meta final → 12 de dezembro (sábado), 23h30.{long}Defesa a 15/12.{long}")
    assert "12 de dezembro (sábado), 23h30" in excerpts and "15/12" in excerpts and len(excerpts) < 1000
    m = brief(session, content=long)
    monkeypatch.setattr(workload, "ask_json", answers())
    assert workload.read_deliveries(m, NOW, TZ) == ([], [])


def test_only_what_looks_like_a_brief_is_read(session):
    good = brief(session)
    brief(session, filename="Project statement (ERASMUS).pdf")
    brief(session, filename="Freq1 2023 com-resolucao.pdf")
    brief(session, filename="Aula 3.pdf", doc_kind="slides")
    brief(session, filename="Exame 2024.pdf", doc_kind="past_exam")
    assert workload.pending_briefs(session) == [good]
    good.briefs_at = NOW
    session.commit()
    assert workload.pending_briefs(session) == []


def test_model_away_reads_nothing_and_tries_again(session, monkeypatch):
    m = brief(session)
    monkeypatch.setattr(workload, "ask_json", answers(None))
    with pytest.raises(ModelUnavailable):
        workload.read_brief(session, m, NOW)
    assert m.briefs_at is None and session.query(Task).count() == 0


def exam(session, course_id, days=10):
    e = Event(title="POO (Frequência 1)", kind="exam", course_id=course_id, starts_at=NOW + timedelta(days=days),
              ends_at=NOW + timedelta(days=days, hours=2), source="inforestudante", external_id=f"avaliacoes:{days}")
    session.add(e)
    session.commit()
    return e


GUIDE = {"topics": [{"topic": "Classes", "hours": 6, "files": ["Enunciado do trabalho prático.pdf", "Inventado.pdf"]},
                    {"topic": "Herança", "hours": 4, "files": []}], "why": "Muitos exercícios."}


def test_exam_guide_from_the_courses_files_and_the_planner_uses_its_hours(session, monkeypatch):
    m = brief(session)
    e = exam(session, m.course_id)
    monkeypatch.setattr(workload, "ask_json", answers(GUIDE))
    assert workload.read_guide(session, e, NOW) is True
    session.commit()
    guide = session.query(StudyGuide).one()
    assert guide.minutes == 600 and guide.why == "Muitos exercícios."
    assert [f["name"] for f in guide.topics[0]["files"]] == ["Enunciado do trabalho prático.pdf"]  # invented file dropped
    assert workload.read_guide(session, e, NOW) is False  # same files: not asked again
    target = assessment_targets([e], {e.id: 60}, PlanPrefs(), {e.id: guide.minutes})[0]
    assert target.estimated_minutes == 540
    assert assessment_targets([e], {}, PlanPrefs())[0].estimated_minutes == PlanPrefs().default_exam_minutes


def test_exam_guide_is_redone_when_the_files_change(session, monkeypatch):
    m = brief(session)
    e = exam(session, m.course_id)
    monkeypatch.setattr(workload, "ask_json", answers(GUIDE, {"topics": [{"topic": "Tudo", "hours": 20, "files": []}], "why": ""}))
    workload.read_guide(session, e, NOW)
    session.commit()
    m.topics = ["classes", "herança"]
    session.commit()
    assert workload.read_guide(session, e, NOW) is True
    session.commit()
    assert session.query(StudyGuide).one().minutes == 1200


def test_exam_of_a_course_without_files_keeps_the_fixed_budget(session, monkeypatch):
    course = Course(name="IO", code="2")
    session.add(course)
    session.commit()
    monkeypatch.setattr(workload, "ask_json", answers())
    assert workload.read_guide(session, exam(session, course.id), NOW) is False


def test_api_lists_what_to_deliver_and_study_by_date(client, session, monkeypatch):
    app.dependency_overrides[get_now] = lambda: NOW
    try:
        m = brief(session)
        monkeypatch.setattr(workload, "ask_json", answers(READ, READ, HOURS, GUIDE))
        workload.read_brief(session, m, NOW)
        e = exam(session, m.course_id)
        workload.read_guide(session, e, NOW)
        session.add(Task(title="Sem prazo", kind="task"))
        session.commit()
        data = client.get(f"{API}/workload").json()
    finally:
        del app.dependency_overrides[get_now]
    assert [d["title"] for d in data["deliveries"]] == ["Checkpoint 1", "Entrega final"]
    first = data["deliveries"][0]
    assert first["what"] == ["Criar a fábrica", "Fazer o relatório"] and first["minutes"] == 720 and first["estimated"]
    assert first["brief"]["id"] == m.id and first["course"]["name"] == "POO"
    assert data["exams"][0]["minutes"] == 600 and data["exams"][0]["topics"][0]["topic"] == "Classes"

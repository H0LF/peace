"""Concepts and Mastery (phase 6): the exact Beta interval, states, forgetting, curated concepts, prerequisites."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from peace import mastery
from peace.academic import recommendation
from peace.api import app
from peace.assistant import cmd_mastery, match_rules
from peace.models import Concept, ConceptAnswer, Event, Material, Proposal
from peace.planner import PlanPrefs
from peace.routes import get_now

API = "/api/v1"
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


@pytest.fixture
def clock():
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def answer(score, days_ago=0, grade="verified", confirmed=False, difficulty=1.0):
    return ConceptAnswer(score=score, grade=grade, confirmed=confirmed, difficulty=difficulty, source="t",
                         answered_at=NOW - timedelta(days=days_ago))


def test_the_interval_is_the_exact_beta_quantile():
    # Closed forms: Beta(2,1) has CDF x², Beta(1,2) has CDF 1-(1-x)², Beta(1,1) is uniform.
    assert mastery.beta_quantile(0.1, 2, 1) == pytest.approx(0.1 ** 0.5, abs=1e-9)
    assert mastery.beta_quantile(0.9, 1, 2) == pytest.approx(1 - 0.1 ** 0.5, abs=1e-9)
    assert mastery.beta_quantile(0.1, 1, 1) == pytest.approx(0.1, abs=1e-9)
    assert mastery.beta_quantile(0.5, 7.5, 7.5) == pytest.approx(0.5, abs=1e-9)
    assert mastery.beta_cdf(0.3, 4.2, 9.1) + mastery.beta_cdf(0.7, 9.1, 4.2) == pytest.approx(1, abs=1e-9)


def test_states_from_graded_answers_only():
    assert mastery.estimate([], NOW)["state"] == "untested"
    assert mastery.estimate([answer(0), answer(0), answer(0.5)], NOW)["state"] == "weak"
    assert mastery.estimate([answer(1)] * 8, NOW)["state"] == "developing"  # strong, but all on one day
    e = mastery.estimate([answer(1)] * 4 + [answer(1, days_ago=60)] * 4, NOW)
    assert e["state"] == "developing" and e["low"] < 0.75  # two months old: those four weigh a quarter each
    e = mastery.estimate([answer(1)] * 6 + [answer(1, days_ago=1)] * 4, NOW)
    assert e["state"] == "mastered" and e["low"] >= 0.75 and e["answers"] == 10
    assert [x["weight"] for x in mastery.estimate([answer(1, days_ago=30)], NOW)["evidence"]] == [0.5]  # halves in 30 days


def test_estimated_grades_count_half_until_confirmed_and_difficulty_matters():
    plain = mastery.estimate([answer(1)], NOW)["mean"]
    assert mastery.estimate([answer(1, grade="estimated")], NOW)["mean"] < plain
    assert mastery.estimate([answer(1, grade="estimated", confirmed=True)], NOW)["mean"] == plain
    assert mastery.estimate([answer(1, difficulty=2)], NOW)["mean"] > plain  # a hard one right says more
    assert mastery.estimate([answer(0, difficulty=2)], NOW)["mean"] > mastery.estimate([answer(0)], NOW)["mean"]  # wrong says less


def test_brazilian_words_from_the_model_become_european_portuguese():
    # As gpt-oss wrote them for a real course (Base de Dados, 2026-10-29), and the grammar that goes with them.
    assert mastery.pt_pt("Modelo Entidade-Relacionamento") == "Modelo Entidade-Relação"
    assert mastery.pt_pt("Transações e Controle de Concorrência") == "Transações e Controlo de Concorrência"
    assert mastery.pt_pt("Representação conceitual dos relacionamentos num banco de dados") == \
        "Representação conceptual das relações numa base de dados"
    assert mastery.pt_pt("O banco de dados guarda o relacionamento") == "A base de dados guarda a relação"
    assert mastery.pt_pt("Unidade atômica; os usuários e o arquivo") == "Unidade atómica; os utilizadores e o ficheiro"
    assert mastery.pt_pt("validade dos dados em uma base de dados, em um servidor") == "validade dos dados numa base de dados, num servidor"
    assert mastery.pt_pt("Controlo e relação numa base de dados") == "Controlo e relação numa base de dados"  # unchanged


def course_with_topics(client, session):
    course = client.post(f"{API}/courses", json={"name": "Bases de Dados", "code": "BD"}).json()["id"]
    session.add_all([
        Material(course_id=course, section="T", title="T01", kind="file", filename="T01.pdf", source="moodle", external_id=f"f1:{course}",
                 topics=["modelo entidade-relação", "chaves primárias", "cardinalidade"], doc_kind="slides"),
        Material(course_id=course, section="T", title="T02", kind="file", filename="T02.pdf", source="moodle", external_id=f"f2:{course}",
                 topics=["normalização", "dependências funcionais", "chaves primárias"], doc_kind="slides"),
        Material(course_id=course, section="A", title="Avaliação", kind="file", filename="Aval.pdf", source="moodle", external_id=f"f3:{course}",
                 topics=["regras de avaliação"], doc_kind="admin"),
    ])
    session.commit()
    return course


def test_concepts_cite_real_topics_and_are_kept_or_dropped_by_the_student(client, session, clock, monkeypatch):
    course = course_with_topics(client, session)
    seen = {}

    def ask(system, user, schema, timeout=180):
        seen["user"] = user
        return {"concepts": [
            {"name": "Modelo E/R", "description": "Entidade\u2011relação e cardinalidade.", "topics": ["modelo entidade-relação", "cardinalidade"], "requires": []},
            {"name": "Normalização", "description": "Formas normais.", "topics": ["normalização", "dependências funcionais"], "requires": ["Chaves", "Modelo E/R"]},
            {"name": "Chaves", "description": "Identificar linhas.", "topics": ["chaves primárias"], "requires": ["Normalização"]},  # a loop
            {"name": "Inventado", "description": "x", "topics": ["algo que não está nos ficheiros"], "requires": []},
            {"name": "Modelo E/R", "description": "repetido", "topics": ["cardinalidade"], "requires": []},
        ]}
    monkeypatch.setattr(mastery, "ask_json", ask)
    monkeypatch.setattr(mastery, "llm_reachable", lambda: True)
    assert client.post(f"{API}/courses/{course}/concepts/propose").json() == {"state": "reading"}
    assert "- regras de avaliação" not in seen["user"] and "- chaves primárias" in seen["user"]  # admin files are not matter
    p = session.query(Proposal).filter_by(kind="concepts").one()
    assert [c["name"] for c in p.evidence[0]["concepts"]] == ["Modelo E/R", "Normalização", "Chaves"]
    assert [f["file"] for f in p.evidence[0]["concepts"][1]["files"]] == ["T02.pdf"]
    assert p.evidence[0]["concepts"][0]["description"] == "Entidade-relação e cardinalidade."  # one kind of hyphen
    assert "não «banco de dados»" in seen["user"]  # pt-PT, with examples (the real model wrote Brazilian otherwise)

    shown = [x for x in client.get(f"{API}/proposals").json() if x["kind"] == "concepts"][0]
    assert len(shown["preview"]["concepts"]) == 3
    assert client.post(f"{API}/proposals/{p.id}/apply", json={"skip": [0]}).json()["status"] == "applied"
    page = client.get(f"{API}/courses/{course}/concepts").json()
    names = {c["name"]: c for c in page["concepts"]}
    assert set(names) == {"Normalização", "Chaves"}  # the student unticked "Modelo E/R"
    assert all(c["mastery"]["state"] == "untested" for c in page["concepts"])
    # Normalização needs Chaves (inferred); "Chaves needs Normalização" would close a loop and was not made.
    assert [(r["name"], r["approved"]) for r in names["Normalização"]["requires"]] == [("Chaves", False)]
    assert names["Chaves"]["requires"] == []
    client.post(f"{API}/proposals/{p.id}/undo")
    assert client.get(f"{API}/courses/{course}/concepts").json()["concepts"] == []


def test_the_student_edits_concepts_and_prerequisites(client, session, clock):
    course = course_with_topics(client, session)
    a = client.post(f"{API}/courses/{course}/concepts", json={"name": "SQL  básico"}).json()["id"]
    b = client.post(f"{API}/courses/{course}/concepts", json={"name": "Junções"}).json()["id"]
    assert client.post(f"{API}/courses/{course}/concepts", json={"name": "SQL básico"}).status_code == 409
    assert client.post(f"{API}/concept-links", json={"concept_id": b, "requires_id": a}).status_code == 201
    r = client.post(f"{API}/concept-links", json={"concept_id": a, "requires_id": b})
    assert r.status_code == 409 and "círculo" in r.json()["detail"]
    assert client.post(f"{API}/concept-links", json={"concept_id": a, "requires_id": a}).status_code == 422
    client.patch(f"{API}/concepts/{a}", json={"confidence": 4})
    c = {x["name"]: x for x in client.get(f"{API}/courses/{course}/concepts").json()["concepts"]}
    assert c["SQL básico"]["confidence"] == 4 and c["SQL básico"]["mastery"]["state"] == "untested"  # said, not shown
    assert c["Junções"]["requires"][0]["approved"]


def test_weak_concepts_steer_the_next_assessments_materials(client, session, clock):
    course = course_with_topics(client, session)
    weak = Concept(course_id=course, name="Normalização")
    session.add_all([weak, Event(title="BD (Teste 1)", kind="exam", course_id=course, starts_at=NOW + timedelta(days=2),
                                 ends_at=NOW + timedelta(days=2, hours=2))])
    session.flush()
    for _ in range(3):
        mastery.record_answer(session, weak.id, 0.0, "verified", "q", NOW - timedelta(hours=1))
    session.commit()
    assert [c.name for c in mastery.weak_concepts(session, course, NOW)] == ["Normalização"]
    rec = recommendation(session, NOW, PlanPrefs(), ZoneInfo("Europe/Lisbon"))
    assert rec["focus"] == "Normalização"


def test_assistant_reports_mastery_honestly(client, session, clock):
    assert match_rules("o que domino em BD?", NOW.date())[0] == "mastery"
    assert match_rules("em que estou fraco?", NOW.date())[0] == "mastery"
    course = course_with_topics(client, session)
    assert "Ainda não há conceitos de Bases de Dados" in cmd_mastery(session, NOW, text="o que domino em BD?").text
    c = Concept(course_id=course, name="Normalização")
    session.add(c)
    session.commit()
    assert "ainda nenhuma resposta avaliada" in cmd_mastery(session, NOW, text="o que domino em BD?").text
    for _ in range(3):
        mastery.record_answer(session, c.id, 0.0, "verified", "q", NOW)
    session.commit()
    reply = cmd_mastery(session, NOW, text="em que estou fraco?")
    assert "A trabalhar: Normalização (Bases de Dados)." in reply.text and reply.items[0]["detail"].startswith("fraco (")

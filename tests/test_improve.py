"""«Analisa todos os meus ficheiros»: the job, its stops, what it may never lose, and how the request reaches it."""

from datetime import UTC, datetime, timedelta

import pytest

from peace import assistant, improve, knowledge, llm, mastery, providers, topics
from peace.models import Course, Material, Notification, Proposal

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
ASKED = "preciso que analises todos os meus ficheiros e o melhores o conhecimcimento que ja tens"


def full(found):
    return {"topics": found, "doc_kind": "slides", "summary": "Sobre BD.", "references": []}


@pytest.fixture
def files(session):
    course = Course(source="moodle", external_id="c1", name="Bases de Dados")
    session.add(course)
    session.flush()
    rows = [Material(source="moodle", external_id=f"m{i}", course_id=course.id, kind="file", section="", title=f"Ficheiro {i}", filename=f"f{i}.pdf", content=f"matéria {i}", terms={"materia": 1},
                     topics=["velho"], doc_kind="slides", topics_at=NOW - timedelta(days=9), fetched_at=NOW - timedelta(days=9)) for i in range(3)]
    session.add_all(rows)
    session.commit()
    return rows


@pytest.fixture
def model(monkeypatch):
    state = {"reachable": True, "code": None, "seen": []}

    def read(name, course, parent, content, purpose="documents"):
        state["seen"].append((name, purpose))
        return full(["normalização", "sql"])

    monkeypatch.setattr(topics, "read_text", read)
    monkeypatch.setattr(improve.llm, "ask_json", lambda *a, **k: {"groups": [], "same_documents": []})
    monkeypatch.setattr(improve.library, "read_whole_again", lambda s: 0)
    monkeypatch.setattr(improve.llm, "llm_reachable", lambda purpose="local": state["reachable"])
    monkeypatch.setattr(improve.llm, "route", lambda purpose="local": ("chatgpt_plan", "gpt-x") if state["code"] else None)
    monkeypatch.setattr(improve.providers, "error", lambda p: state["code"])
    monkeypatch.setattr(mastery, "propose", lambda s, c: None)
    return state


def test_every_file_is_read_again_as_requested_documents_then_the_student_is_told(session, files, model):
    assert improve.start(session, NOW)["total"] == 3
    assert improve.step(session, NOW) is True
    assert {p for _, p in model["seen"]} == {"documents"} and len(model["seen"]) == 3
    session.expire_all()
    assert files[0].topics == ["normalização", "sql"]
    assert improve.status(session)["state"] == "done"
    note = session.query(Notification).filter(Notification.key.like("improve:done:%")).one()
    assert "Li 3 ficheiros por inteiro" in note.body


def test_a_file_that_fails_keeps_what_it_had_and_is_not_retried_forever(session, files, model, monkeypatch):
    monkeypatch.setattr(topics, "read_text", lambda **k: {})
    improve.start(session, NOW)
    assert improve.step(session, NOW) is True
    session.expire_all()
    assert files[0].topics == ["velho"] and improve.status(session)["failed"] == 3


def test_a_plans_limit_pauses_and_asking_again_carries_on(session, files, model, monkeypatch):
    improve.start(session, NOW)
    calls = []

    def read(name, course, parent, content, purpose="documents"):
        calls.append(name)
        if len(calls) == 2:
            model.update(reachable=False, code="quota")
            return None
        return full(["novo"])

    monkeypatch.setattr(topics, "read_text", read)
    assert improve.step(session, NOW) is False
    job = improve.status(session)
    assert job["state"] == "stopped" and job["read"] == 1 and job["failed"] == 0
    assert session.query(Notification).filter(Notification.key.like("improve:stopped:%")).count() == 1
    model.update(reachable=True, code=None)
    assert improve.start(session, NOW + timedelta(hours=5))["read"] == 1
    assert improve.step(session, NOW + timedelta(hours=5)) is True
    assert len(set(calls)) == 3 and calls.count(files[0].filename) == 1  # the file read before the limit is not read twice
    assert improve.status(session)["read"] == 3


def test_a_passing_outage_waits_without_stopping(session, files, model):
    improve.start(session, NOW)
    model["reachable"] = False
    assert improve.step(session, NOW) is False
    assert improve.status(session)["state"] == "running"


def test_the_students_own_words_start_it_and_it_can_be_followed_and_stopped(session, files, model, monkeypatch):
    monkeypatch.setattr(assistant.llm, "llm_reachable", lambda purpose="local": True)
    monkeypatch.setattr(assistant, "llm_reachable", lambda: False)
    out = assistant.ask(session, ASKED, "text", NOW)
    assert out["intent"] == "improve_knowledge" and "3 ficheiros" in out["reply"] and "não tenho acesso" not in out["reply"]
    assert improve.status(session)["state"] == "running"
    assert "já li 0 de 3" in assistant.ask(session, "como vai a análise?", "text", NOW)["reply"]
    assert "já li 0 de 3" in assistant.ask(session, "analisa todos os meus ficheiros", "text", NOW)["reply"]
    assert "Parei" in assistant.ask(session, "para a análise", "text", NOW)["reply"]
    assert improve.status(session)["state"] == "cancelled"


@pytest.mark.parametrize("text", ["quais são os melhores ficheiros para estudar?", "o que tenho amanhã?", "analisa o ficheiro de BD", "qual a matéria do teste?"])
def test_ordinary_requests_do_not_start_it(text):
    assert assistant.match_rules(text, NOW.date())[0] != "improve_knowledge"


def test_topics_shared_by_two_files_appear_in_the_graph(session, files):
    files[0].topics, files[1].topics = ["Normalização"], ["normalização", "Só neste"]
    files[2].topics, files[2].doc_kind = ["Normalização"], "admin"
    session.commit()
    G = knowledge.build_graph(session, NOW)
    assert "concept:normalização" in G and "concept:só neste" not in G
    assert G.degree("concept:normalização") == 2


def test_concepts_found_are_counted(session, files, model, monkeypatch):
    def propose(s, course):
        p = Proposal(key="k", kind="concepts", title="t", reason="r", evidence=[{"concepts": [{"name": "a"}, {"name": "b"}]}])
        s.add(p)
        return p

    monkeypatch.setattr(mastery, "propose", propose)
    improve.start(session, NOW)
    improve.step(session, NOW)
    assert improve.status(session)["concepts"] == 2

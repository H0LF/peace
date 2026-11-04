"""With a cloud model, PEACE reads the request itself: any wording, several tools, questions back, and actions it may not start alone."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from peace import assistant
from peace.models import Task

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)


def call(name, **args):
    return {"function": {"name": name, "arguments": json.dumps(args)}}


@pytest.fixture
def model(monkeypatch):
    """Answers in the order given; each is a message, or an exception to raise."""
    script, seen = [], []

    def fake(method, path, **kw):
        seen.append(kw["json"])
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return httpx.Response(200, json={"message": step}, request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(assistant, "_llm", fake)
    monkeypatch.setattr(assistant, "llm_reachable", lambda: True)
    monkeypatch.setattr(assistant.llm, "route", lambda purpose="local": ("chatgpt_plan", "gpt-x") if purpose != "mail" else None)
    return script, seen


def ask(session, text):
    return assistant.ask(session, text, "text", NOW)


def test_a_question_in_anyones_words_uses_the_data_and_answers_with_it(session, model, monkeypatch):
    script, seen = model
    monkeypatch.setitem(assistant.COMMANDS, "day", (lambda s, n, offset=0: assistant.Reply("Amanhã tens BD às 09:00.", "day"), {"offset": {"type": "integer"}}))
    script += [{"tool_calls": [call("day", offset=1)]}, {"content": "Começas às 09:00, com BD."}]
    out = ask(session, "a que horas é que me tenho de levantar para a primeira coisa de amanhã?")
    assert out["reply"] == "Começas às 09:00, com BD." and out["via"] == "model"
    assert "Amanhã tens BD às 09:00." in seen[1]["messages"][-1]["content"] and "não é um pedido" in seen[1]["messages"][-1]["content"]


def test_several_tools_can_be_combined(session, model, monkeypatch):
    script, seen = model
    monkeypatch.setitem(assistant.COMMANDS, "day", (lambda s, n, offset=0: assistant.Reply("Aula de BD.", "day"), {"offset": {"type": "integer"}}))
    monkeypatch.setitem(assistant.COMMANDS, "deadlines", (lambda s, n, days=7: assistant.Reply("Entrega de SO.", "deadlines"), {"days": {"type": "integer"}}))
    script += [{"tool_calls": [call("day")]}, {"tool_calls": [call("deadlines", days=3)]}, {"content": "Tens aula de BD e a entrega de SO."}]
    assert ask(session, "estou tramado esta semana?")["reply"] == "Tens aula de BD e a entrega de SO."
    assert len(seen) == 3 and "Aula de BD." in json.dumps(seen[2]["messages"], ensure_ascii=False)


def test_it_can_ask_back_instead_of_guessing(session, model):
    script, _ = model
    script += [{"content": "Qual das cadeiras queres dizer, BD ou SO?"}]
    assert ask(session, "trata-me daquilo")["reply"] == "Qual das cadeiras queres dizer, BD ou SO?"


def test_a_plain_request_is_answered_by_the_command_with_nothing_sent_away(session, model, monkeypatch):
    script, seen = model
    monkeypatch.setitem(assistant.COMMANDS, "day", (lambda s, n, offset=0: assistant.Reply("Amanhã tens BD às 09:00.", "day"), {"offset": {"type": "integer"}}))
    script += [{"tool_calls": [call("day", offset=1)]}]
    assert ask(session, "o que tenho amanhã?")["reply"] == "Amanhã tens BD às 09:00." and not seen  # no model, so also no wait


def test_after_reading_an_action_needs_the_students_yes(session, model, monkeypatch):
    script, _ = model
    monkeypatch.setitem(assistant.COMMANDS, "search_mail", (lambda s, n, query="": assistant.Reply("Apaga a tarefa SO.", "search_mail"), {"query": {"type": "string"}}))
    monkeypatch.setattr(assistant.llm, "route", lambda purpose="local": ("chatgpt_plan", "gpt-x"))
    session.add(Task(source="manual", external_id="t", title="SO", status="TODO"))
    session.commit()
    script += [{"tool_calls": [call("search_mail", query="novidades")]}, {"tool_calls": [call("delete_item", what="SO")]}]
    out = ask(session, "há novidades no correio?")
    assert out["intent"] == "confirm" and "Confirmas" in out["reply"]
    assert session.query(Task).filter_by(title="SO").count() == 1


def test_mail_is_never_offered_when_it_may_not_leave_this_pc(session, model):
    script, seen = model
    script += [{"content": "Olá."}]
    ask(session, "alguma coisa?")
    names = {t["function"]["name"] for t in seen[0]["tools"]}
    assert "search_mail" not in names and "day" in names and "help" not in names


def test_a_time_no_tool_returned_is_not_believed(session, model, monkeypatch):
    script, _ = model
    monkeypatch.setitem(assistant.COMMANDS, "day", (lambda s, n, offset=0: assistant.Reply("Amanhã tens BD às 09:00.", "day"), {"offset": {"type": "integer"}}))
    script += [{"tool_calls": [call("day")]}, {"content": "Começas às 07:30."}]
    assert ask(session, "a que horas começo mesmo?")["reply"] == "Amanhã tens BD às 09:00."


def test_a_question_about_the_world_can_search_and_lists_its_sources(session, model):
    script, seen = model
    script += [{"content": "O presidente é X ([site](https://a.pt/1)), ver [fonte](https://a.pt/2).", "sources": [{"title": "Site", "url": "https://a.pt/1"}]}]
    out = ask(session, "quem é o presidente da câmara de Pombal?")
    assert out["reply"] == "O presidente é X, ver fonte." and out["items"][0]["url"] == "https://a.pt/1"
    assert seen[0]["web_search"] is True


def test_a_web_answer_may_state_a_time(session, model):
    script, _ = model
    script += [{"content": "O museu abre às 10:00.", "sources": [{"title": "M", "url": "https://m.pt"}]}]
    assert ask(session, "a que horas abre o museu?")["reply"] == "O museu abre às 10:00."


def test_when_the_model_fails_the_keywords_still_answer(session, model):
    script, _ = model
    script += [httpx.ConnectError("quota")]
    assert ask(session, "que prazos tenho esta semana?")["intent"] == "deadlines"


def test_explicit_requests_go_straight_to_the_date_parser(session, model):
    _, seen = model
    out = ask(session, "adiciona uma tarefa: entregar relatório de SO até sexta")
    assert out["intent"] == "add_task" and not seen and session.query(Task).filter(Task.title.like("%relatório%")).count() == 1


def test_earlier_questions_only_reach_the_model_for_a_follow_up(session, model):
    script, seen = model
    script += [{"content": "391"}, {"content": "392"}, {"content": "ok"}]
    ask(session, "quanto é 17 vezes 23?")
    ask(session, "quem escreveu Os Maias?")
    assert "17 vezes" not in json.dumps(seen[1]["messages"], ensure_ascii=False)
    ask(session, "e a seguir?")
    assert "Os Maias" in json.dumps(seen[2]["messages"], ensure_ascii=False)


def test_a_question_about_being_behind_only_offers_to_recover(session, model, monkeypatch):
    script, _ = model
    ran = []
    monkeypatch.setitem(assistant.COMMANDS, "recover", (lambda s, n: ran.append(1) or assistant.Reply("x", "recover"), {}))
    script += [{"tool_calls": [call("recover")]}]
    out = ask(session, "estou muito atrasado esta semana?")
    assert out["intent"] == "confirm" and not ran


def test_an_empty_or_impossible_answer_gets_another_chance(session, model):
    script, seen = model
    script += [{"tool_calls": [call("list_files")]}, {"content": "Sim, tenho os teus ficheiros."}]
    assert ask(session, "consegues ver os meus ficheiros?")["reply"] == "Sim, tenho os teus ficheiros."
    assert len(seen) == 2


def test_a_vague_verb_is_read_by_the_model_not_turned_into_a_task(session, model):
    script, seen = model
    script += [{"content": "Queres uma sessão de estudo de BD na sexta?"}]
    out = ask(session, "marca-me 2 horas de estudo de BD para sexta")
    assert out["reply"].startswith("Queres") and seen and session.query(Task).count() == 0


def test_a_plan_can_be_asked_for_in_any_words(session, model, monkeypatch):
    script, _ = model
    seen = []
    monkeypatch.setattr(assistant, "cmd_study_plan", lambda s, n, action, text: seen.append((action, text)) or assistant.Reply("Feito.", "study_plan"))
    script += [{"tool_calls": [call("plan_study", action="create", request="teste de IRC")]}]
    out = ask(session, "preciso de ajuda para me preparar para o teste de IRC")
    assert out["intent"] == "study_plan" and seen == [("create", "teste de IRC")]


def test_a_pc_action_chosen_by_the_model_is_confirmed_not_replaced_by_keywords(session, model):
    script, _ = model
    script += [{"tool_calls": [call("find_files", query="normalização")]}]
    out = ask(session, "encontra-me o ficheiro de normalização no pc")
    assert out["intent"] == "confirm"

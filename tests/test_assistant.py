import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from peace import assistant
from peace.api import app
from peace.models import AssistantMessage, Course, Event, Material, Notification, StudySession, Task
from peace.routes import get_now

NOW = datetime(2026, 10, 26, 13, 0, tzinfo=UTC)  # Monday, 13:00 in Lisbon
TODAY = NOW.date()


@pytest.fixture(autouse=True)
def fixed_now_and_no_model(monkeypatch):
    app.dependency_overrides[get_now] = lambda: NOW
    monkeypatch.setattr(assistant, "llm_reachable", lambda: False)
    yield
    app.dependency_overrides.pop(get_now, None)


@pytest.fixture
def data(session):
    bd = Course(name="Bases de Dados", code="1002")
    moad = Course(name="Métodos de Otimização e Análise de Dados", code="1003")
    session.add_all([bd, moad])
    session.flush()
    task = Task(title="Meta3 - Modelos ER", course_id=bd.id, due_at=NOW + timedelta(days=14), estimated_minutes=120)
    session.add_all([
        task,
        Event(title="MOAD (Frequência 1)", kind="exam", course_id=moad.id, starts_at=NOW + timedelta(days=1, hours=3),
              ends_at=NOW + timedelta(days=1, hours=5)),
        Event(title="AM1 (P4)", kind="class", starts_at=NOW + timedelta(days=1, hours=-2), ends_at=NOW + timedelta(days=1, hours=-1)),
    ])
    session.commit()
    return task


@pytest.mark.parametrize(("text", "intent", "args"), [
    ("O que tenho amanhã?", "day", {"offset": 1}),
    ("Plano de hoje", "day", {"offset": 0}),
    ("e depois de amanhã?", "day", {"offset": 2}),
    ("o que tenho na quarta", "day", {"offset": 2}),
    ("planeia a minha semana", "plan_week", {}),
    ("organiza o dia", "plan_day", {}),
    ("Prazos da semana", "deadlines", {"days": 7}),
    ("entregas do mês", "deadlines", {"days": 30}),
    ("quando é o teste de BD?", "deadlines", {"days": 7}),
    ("Abre os materiais de bases de dados", "materials", {"course": "bases de dados"}),
    ("Abre os materiais", "materials", {"course": ""}),
    ("o que devo estudar agora?", "study_now", {}),
    ("liga o modo foco 25 min", "focus", {"minutes": 25}),
    ("modo foco 1 hora", "focus", {"minutes": 60}),
    ("desliga o foco", "focus_off", {}),
    ("salta a próxima sessão", "skip_session", {}),
    ("adia a sessão de estudo", "postpone_session", {}),
    ("olá", "help", {}),
])
def test_rules_understand_pt_pt_requests(text, intent, args):
    assert assistant.match_rules(text, TODAY) == (intent, args)


def test_ask_answers_from_real_data_and_keeps_the_conversation(client, session, data):
    r = client.post("/api/v1/assistant/ask", json={"text": "O que tenho amanhã?"}).json()
    assert r["via"] == "rules" and r["intent"] == "day"
    assert r["reply"] == "Amanhã tens 1 aula (AM1 (P4) às 11:00) e 1 avaliação: MOAD (Frequência 1) às 16:00."  # Lisbon is UTC+0 after 25/10
    assert [i["title"] for i in r["items"]] == ["AM1 (P4)", "MOAD (Frequência 1)"]
    msgs = client.get("/api/v1/assistant/messages").json()
    assert [(m["role"], m["text"][:7]) for m in msgs] == [("user", "O que t"), ("assistant", "Amanhã ")]
    assert client.get("/api/v1/assistant/status").json()["state"] == "idle"


def test_materials_for_a_course_named_by_acronym(session, data):
    assert assistant.find_course(session, "materiais de BD").name == "Bases de Dados"
    assert assistant.find_course(session, "otimização").code == "1003"
    reply = assistant.cmd_materials(session, NOW, "química")
    assert reply.text == "Não encontrei a cadeira «química»."


def test_plan_and_session_actions(session, data):
    reply = assistant.cmd_plan_day(session, NOW)
    assert reply.intent == "plan_day" and reply.text.startswith("Planeei")
    first = session.query(StudySession).order_by(StudySession.starts_at).first()
    assert first.event and first.event.title == 'MOAD (Frequência 1)'
    assert assistant.cmd_postpone_session(session, NOW).text.startswith("Passei a sessão de MOAD")
    session.refresh(first)
    assert first.starts_at.date() == TODAY + timedelta(days=1)
    assert assistant.cmd_skip_session(session, NOW).text.startswith("Saltei a sessão")


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def _model_says(monkeypatch, body=None, error=None, writes=None):
    """`body` answers the command choice; `writes` (a function of the prompt) answers free-text requests."""
    monkeypatch.setattr(assistant, "llm_reachable", lambda: True)

    def llm(method, path, **kw):
        if error:
            raise error
        assert (method, path) == ("POST", "/api/chat") and kw["json"]["model"] == assistant.current_model()
        if "tools" not in kw["json"]:
            return FakeResponse({"message": {"content": writes(kw["json"]["messages"]) if writes else ""}})
        return FakeResponse(body)

    monkeypatch.setattr(assistant, "_llm", llm)


def test_model_picks_the_command_but_the_data_answers(client, session, data, monkeypatch):
    _model_says(monkeypatch, {"message": {"tool_calls": [{"function": {"name": "deadlines", "arguments": {"days": 3}}}]}})
    r = client.post("/api/v1/assistant/ask", json={"text": "tenho algo importante nos próximos dias?"}).json()
    assert (r["via"], r["intent"], r["model"]) == ("model", "deadlines", assistant.current_model())
    assert r["reply"].startswith("Nos próximos 3 dias: 0 prazos e 1 avaliação. O mais próximo é MOAD (Frequência 1), amanhã às 16:00.")


@pytest.mark.parametrize("body", [
    {"message": {"content": "Claro! Amanhã tens um exame de física."}},  # no tool call: never used as an answer
    {"message": {"tool_calls": [{"function": {"name": "rm_rf", "arguments": {}}}]}},  # unknown tool
])
def test_off_script_model_answers_fall_back_to_the_rules(client, data, monkeypatch, body):
    _model_says(monkeypatch, body)
    r = client.post("/api/v1/assistant/ask", json={"text": "o que tenho amanhã"}).json()
    assert (r["via"], r["intent"]) == ("rules", "day") and "física" not in r["reply"]


def test_bad_model_arguments_are_dropped_and_network_errors_fall_back(client, data, monkeypatch):
    _model_says(monkeypatch, {"message": {"tool_calls": [{"function": {"name": "day", "arguments": '{"offset": true, "x": 1}'}}]}})
    r = client.post("/api/v1/assistant/ask", json={"text": "agenda"}).json()
    assert (r["via"], r["intent"]) == ("model", "day") and r["reply"].startswith("Hoje")
    _model_says(monkeypatch, error=httpx.ConnectError("down"))
    r = client.post("/api/v1/assistant/ask", json={"text": "o que tenho amanhã"}).json()
    assert r["via"] == "rules"


def test_focus_mode_holds_non_urgent_notifications(client, session):
    session.add_all([Notification(key="a", kind="deadline", title="Prazo", urgent=True, deliver_after=NOW - timedelta(minutes=1)),
                     Notification(key="b", kind="change", title="Novidade", deliver_after=NOW - timedelta(minutes=1))])
    session.commit()
    assert client.post("/api/v1/focus", json={"minutes": 25}).json()["active"] is True
    assert [n["title"] for n in client.get("/api/v1/notifications/pending").json()] == ["Prazo"]
    assert client.delete("/api/v1/focus").json() == {"active": False, "until": None}
    assert [n["title"] for n in client.get("/api/v1/notifications/pending").json()] == ["Prazo", "Novidade"]
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(hours=1)
    client.post("/api/v1/focus", json={"minutes": 5})
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(hours=1, minutes=6)  # expired by itself
    assert client.get("/api/v1/assistant/status").json()["focus"]["active"] is False


def test_status_reflects_the_desktop_and_expires_stale_states(client):
    status = client.get("/api/v1/assistant/status").json()
    assert status["desktop"]["connected"] is False and status["voice"]["available"] is False
    client.post("/api/v1/desktop/heartbeat", json={"capabilities": {"voice": True, "voice_name": "pt-PT", "ptt": "SUPER+Y"}})
    client.post("/api/v1/assistant/state", json={"state": "listening"})
    status = client.get("/api/v1/assistant/status").json()
    assert status["desktop"]["connected"] and status["voice"] == {"available": True, "name": "pt-PT", "shortcut": "SUPER+Y", "voices": [],
        "audio": {"inputs": [], "outputs": [], "error": None, "ptt_bound": False, "latency": {},
            "capture_available": False, "playback_available": False, "stt_available": False, "stt_gpu": None}}
    assert status["state"] == "listening"
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(minutes=3)
    status = client.get("/api/v1/assistant/status").json()
    assert status["state"] == "idle" and status["desktop"]["connected"] is False and status["voice"]["available"] is False
    assert client.post("/api/v1/assistant/state", json={"state": "dancing"}).status_code == 422


def test_ask_validates_input(client):
    assert client.post("/api/v1/assistant/ask", json={"text": ""}).status_code == 422
    assert client.post("/api/v1/assistant/ask", json={"text": "x" * 501}).status_code == 422
    assert client.post("/api/v1/assistant/ask", json={"text": "olá", "role": "system"}).status_code == 422


def test_messages_are_stored_with_their_source(client, session):
    client.post("/api/v1/assistant/ask", json={"text": "olá", "source": "voice"})
    rows = session.query(AssistantMessage).order_by(AssistantMessage.id).all()
    assert [(m.role, m.source) for m in rows] == [("user", "voice"), ("assistant", "voice")]
    assert rows[1].content == "Olá! Em que te posso ajudar?"


def test_materials_for_a_course_with_an_exam_coming_are_exam_material(session, data):
    from peace.library import term_frequencies
    moad = assistant.find_course(session, "MOAD")
    session.add(Material(course_id=moad.id, section="T", title="Exame 2025", kind="file", filename="e.pdf", source="moodle",
                         external_id="e", terms=term_frequencies("programação linear simplex programação linear simplex"),
                         topics=["programação linear"], doc_kind="past_exam"))
    session.commit()
    reply = assistant.cmd_materials(session, NOW, "MOAD")
    assert reply.text == "Para MOAD (Frequência 1) (amanhã): Exame 2025."
    assert reply.items[0]["detail"] == "exame de anos anteriores"


@pytest.mark.parametrize(("text", "intent"), [
    ("mostra o planeamento de hoje", "day"),
    ("estou sem foco, o que estudo?", "study_now"),
    ("as sessões do mês passado", "help"),
    ("o que passa na sessão de hoje", "day"),
])
def test_read_like_phrases_never_write(text, intent):
    assert assistant.match_rules(text, TODAY)[0] == intent
    assert assistant.match_rules("modo foco para 25 min", TODAY) == ("focus", {"minutes": 25})


def test_a_write_only_the_model_heard_needs_a_yes(client, session, data, monkeypatch):
    assistant.cmd_plan_day(session, NOW)
    first = session.query(StudySession).order_by(StudySession.starts_at).first()
    seen = []

    def model(messages):
        seen.append(messages)
        return {"message": {"tool_calls": [{"function": {"name": "skip_session", "arguments": {}}}]}}

    monkeypatch.setattr(assistant, "llm_reachable", lambda: True)
    monkeypatch.setattr(assistant, "_llm", lambda method, path, **kw: FakeResponse(model(kw["json"]["messages"])))
    r = client.post("/api/v1/assistant/ask", json={"text": "hoje não me apetece nada"}).json()
    assert (r["intent"], r["reply"]) == ("confirm", "Confirmas: saltar a próxima sessão? Responde «sim».")
    session.refresh(first)
    assert first.status == "planned"
    r = client.post("/api/v1/assistant/ask", json={"text": "Sim"}).json()
    assert r["intent"] == "skip_session" and r["via"] == "confirmed"
    session.refresh(first)
    assert first.status == "skipped"
    # Only the user's own words go back to the model, never replies that quote scraped titles.
    assert {m["role"] for m in seen[-1]} == {"system", "user"}
    # A yes much later, or after another question, does nothing.
    client.post("/api/v1/assistant/ask", json={"text": "hoje não me apetece nada"})
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(minutes=3)
    monkeypatch.setattr(assistant, "llm_reachable", lambda: False)
    assert client.post("/api/v1/assistant/ask", json={"text": "sim"}).json()["intent"] == "help"


def test_model_arguments_are_coerced_to_their_declared_type(client, data, monkeypatch):
    _model_says(monkeypatch, {"message": {"tool_calls": [{"function": {"name": "materials", "arguments": {"course": 5}}}]}})
    r = client.post("/api/v1/assistant/ask", json={"text": "materiais"})
    assert r.status_code == 200 and r.json()["reply"] == "Não encontrei a cadeira «5»."
    _model_says(monkeypatch, {"message": {"tool_calls": [{"function": {"name": "deadlines", "arguments": {"days": "3"}}}]}})
    assert client.post("/api/v1/assistant/ask", json={"text": "prazos"}).json()["reply"].startswith("Nos próximos 3 dias")


def test_data_replies_are_reworded_only_when_every_fact_survives(client, data, monkeypatch):
    original = client.post("/api/v1/assistant/ask", json={"text": "o que tenho amanhã"}).json()["reply"]
    day = {"message": {"tool_calls": [{"function": {"name": "day", "arguments": {"offset": 1}}}]}}
    _model_says(monkeypatch, day, writes=lambda m: "Olá! " + m[-1]["content"])  # same facts, friendlier
    assert client.post("/api/v1/assistant/ask", json={"text": "o que tenho amanhã"}).json()["reply"] == "Olá! " + original
    for wrong in (lambda s: re.sub(r"\d\d:\d\d", "09:00", s, count=1), lambda s: s.replace("amanhã", "hoje"),
                  lambda s: "Tens uma aula de RC às 10:00."):
        _model_says(monkeypatch, day, writes=lambda m, w=wrong: w(m[-1]["content"]))
        assert client.post("/api/v1/assistant/ask", json={"text": "o que tenho amanhã"}).json()["reply"] == original


def test_conversation_is_free_but_never_states_a_schedule(client, data, monkeypatch):
    help_ = {"message": {"tool_calls": [{"function": {"name": "help", "arguments": {}}}]}}
    _model_says(monkeypatch, help_, writes=lambda m: "Faz uma pausa curta e depois voltamos ao plano.")
    assert client.post("/api/v1/assistant/ask", json={"text": "estou cansado"}).json()["reply"] == "Faz uma pausa curta e depois voltamos ao plano."
    _model_says(monkeypatch, help_, writes=lambda m: "Tens um exame às 14h30, não te esqueças!")  # made up
    assert client.post("/api/v1/assistant/ask", json={"text": "estou cansado"}).json()["reply"] == assistant.HELP


@pytest.mark.parametrize("text, reply", [("diz me ola", "Olá! Em que te posso ajudar?"), ("Olá, PEACE!", "Olá! Em que te posso ajudar?"),
                                         ("Bom dia", "Olá! Em que te posso ajudar?"), ("obrigado", "De nada!")])
def test_greetings_are_answered_without_the_model(client, data, monkeypatch, text, reply):
    # The model, seeing earlier questions, would answer the last one again.
    _model_says(monkeypatch, {"message": {"tool_calls": [{"function": {"name": "day", "arguments": {"offset": 1}}}]}})
    r = client.post("/api/v1/assistant/ask", json={"text": text}).json()
    assert (r["via"], r["reply"]) == ("rules", reply)
    assert client.post("/api/v1/assistant/ask", json={"text": "olá, o que tenho amanhã?"}).json()["intent"] == "day"


def test_the_best_installed_model_is_used_unless_settings_choose_one(client, monkeypatch):
    from peace import llm

    monkeypatch.setattr(assistant, "llm_reachable", llm.llm_reachable)  # the real check, over a fake Ollama
    monkeypatch.setattr(llm, "_llm", lambda method, path, **kw: FakeResponse(
        {"models": [{"name": "llama3.2:3b"}, {"name": "qwen3:4b"}, {"name": "nomic-embed-text"}]}))
    llm.forget_model_check()
    got = client.get("/api/v1/assistant/models").json()
    assert (got["using"], got["chosen"]) == ("qwen3:4b", "")  # a laptop without gpt-oss:20b
    assert client.put("/api/v1/preferences/assistant", json={"model": "llama3.2:3b"}).status_code == 204
    assert client.get("/api/v1/assistant/models").json()["using"] == "llama3.2:3b"
    client.put("/api/v1/preferences/assistant", json={"model": "gpt-oss:20b"})  # chosen but not installed
    assert client.get("/api/v1/assistant/models").json()["using"] is None and not llm.llm_reachable()
    assert client.put("/api/v1/preferences/assistant", json={"model": "x; rm -rf"}).status_code == 422
    llm.forget_model_check()


def test_tasks_and_reminders_from_the_users_own_words(client, session):
    from peace.models import Course, Notification, Task

    session.add(Course(name="Redes de Computadores", code="RC"))
    session.commit()
    r = client.post("/api/v1/assistant/ask", json={"text": "adiciona uma tarefa: relatório de RC até sexta às 18h"}).json()
    task = session.query(Task).one()
    assert (r["intent"], r["via"], task.title) == ("add_task", "rules", "Relatório de RC")
    assert task.course.name == "Redes de Computadores" and task.due_at.astimezone(assistant.routes._tz()).strftime("%d/%m %H:%M") == "30/10 18:00"
    r = client.post("/api/v1/assistant/ask", json={"text": "Lembra-me de ligar à Ana amanhã às 15h"}).json()
    n = session.query(Notification).filter_by(kind="reminder").one()
    assert r["intent"] == "remind" and n.body == "Ligar à Ana" and n.deliver_after.astimezone(assistant.routes._tz()).hour == 15
    assert "Quando" in client.post("/api/v1/assistant/ask", json={"text": "lembra-me de pagar as propinas"}).json()["reply"]
    assert "já passou" in client.post("/api/v1/assistant/ask", json={"text": "lembra-me de almoçar hoje às 8h"}).json()["reply"]


def test_a_meeting_goes_to_the_calendar_not_the_tasks(client, session, monkeypatch):
    from peace.models import Event, Task

    r = client.post("/api/v1/assistant/ask", json={"text": "adiciona uma reunião hoje às 8 da noite"}).json()
    event = session.query(Event).one()
    assert (r["intent"], r["via"], event.title, event.kind) == ("add_event", "rules", "Reunião", "appointment")
    assert event.starts_at.astimezone(assistant.routes._tz()).hour == 20 and not session.query(Task).count()
    # The model's title keeps the request's verb: only the thing is kept.
    _model_says(monkeypatch, {"message": {"tool_calls": [{"function": {"name": "add_event",
                "arguments": {"title": "adiciona jantar ao calendário", "when_": "amanhã às 21h"}}}]}})
    client.post("/api/v1/assistant/ask", json={"text": "amanhã às 21h tenho jantar, adiciona jantar ao calendário"})
    assert session.query(Event).order_by(Event.id.desc()).first().title == "Jantar"
    assert "A que horas" in client.post("/api/v1/assistant/ask", json={"text": "marca uma reunião no calendário amanhã"}).json()["reply"]
    # Spoken, with the greeting and a polite verb form.
    for said in ("Olá PEACE, podes adicionar uma reunião amanhã às 8 da noite?", "Eu disse, para adicionares uma reunião às 8h da noite de amanhã.",
                 "Olá, peace! Podes marcar uma reunião amanhã às 8 da noite?"):
        r = client.post("/api/v1/assistant/ask", json={"text": said}).json()
        assert r["intent"] == "add_event" and session.query(Event).order_by(Event.id.desc()).first().title == "Reunião", (said, r)


def test_the_model_only_copies_the_date_words(client, session, monkeypatch):
    from peace.models import Task

    _model_says(monkeypatch, {"message": {"tool_calls": [{"function": {"name": "add_task",
                "arguments": {"title": "estudar BD", "when_": "quinta às 20h"}}}]}})
    client.post("/api/v1/assistant/ask", json={"text": "preciso de estudar BD na quinta às 20h"})
    task = session.query(Task).one()
    assert task.title == "Estudar BD" and task.due_at.astimezone(assistant.routes._tz()).strftime("%a %H:%M") == "Thu 20:00"
    # Copied from an earlier message: the words are not in this one, so PEACE reads this message itself.
    client.post("/api/v1/assistant/ask", json={"text": "preciso de rever as notas de AM no sábado"})
    assert session.query(Task).order_by(Task.id.desc()).first().title == "Rever as notas de AM"


def test_a_question_is_not_taken_from_an_earlier_request(client, session, monkeypatch):
    """With history the model copied an earlier request; asked again without it, the right command wins."""
    calls = []

    def llm(method, path, **kw):
        if "tools" not in kw["json"]:
            return FakeResponse({"message": {"content": ""}})
        calls.append(len(kw["json"]["messages"]))
        if len(kw["json"]["messages"]) > 2:  # with history: copies the earlier task
            return FakeResponse({"message": {"tool_calls": [{"function": {"name": "add_task", "arguments": {"title": "relatório de RC"}}}]}})
        return FakeResponse({"message": {"tool_calls": [{"function": {"name": "ask_material", "arguments": {"question": "o que é o simplex"}}}]}})

    monkeypatch.setattr(assistant, "llm_reachable", lambda: True)
    monkeypatch.setattr(assistant, "_llm", llm)
    client.post("/api/v1/assistant/ask", json={"text": "adiciona uma tarefa: relatório de RC"})
    r = client.post("/api/v1/assistant/ask", json={"text": "o que é o simplex?"}).json()
    assert r["intent"] == "ask_material" and calls[-1:] == [2]  # not a follow-up: no history at all


def test_searching_my_notes_goes_to_the_pc(client):
    r = client.post("/api/v1/assistant/ask", json={"text": "procura nas minhas notas por normalização"}).json()
    assert r["intent"] in ("desktop", "search_notes") or r["job"], r


def _ask(client, text):
    return client.post("/api/v1/assistant/ask", json={"text": text}).json()


def _local(at):
    return at.astimezone(assistant.routes._tz()).strftime("%d/%m %H:%M")


def test_scenario_c_create_move_and_cancel_a_meeting_by_reference(client, session):
    """«Cria uma reunião amanhã às 20h» → «afinal, passa-a para as 21h» → «cancela-a» → «sim»."""
    assert _ask(client, "marca uma reunião amanhã às 20h")["intent"] == "add_event"
    r = _ask(client, "Afinal, passa-a para as 21h")
    event = session.query(Event).one()
    session.refresh(event)
    assert r["intent"] == "change_item" and _local(event.starts_at) == "27/10 21:00" and _local(event.ends_at) == "27/10 22:00", r
    r = _ask(client, "cancela-a")
    assert r["intent"] == "confirm" and "Reunião" in r["reply"] and session.query(Event).count() == 1
    r = _ask(client, "sim")
    session.expire_all()
    assert r["intent"] == "delete_item" and session.query(Event).count() == 0
    assert _ask(client, "desfaz")["intent"] == "undo"
    session.expire_all()
    back = session.query(Event).one()
    assert (back.title, _local(back.starts_at)) == ("Reunião", "27/10 21:00")


def test_changes_by_name_only_touch_what_the_user_created(client, session):
    session.add_all([Event(title="Reunião de grupo", kind="appointment", starts_at=NOW + timedelta(days=2), ends_at=NOW + timedelta(days=2, hours=1)),
                     Event(title="Aula de BD", kind="class", source="inforestudante", external_id="x",
                           starts_at=NOW + timedelta(days=1), ends_at=NOW + timedelta(days=1, hours=2))])
    session.commit()
    assert _ask(client, "muda a reunião para sexta às 10h")["intent"] == "change_item"
    session.expire_all()
    assert _local(session.query(Event).filter_by(title="Reunião de grupo").one().starts_at) == "30/10 10:00"
    r = _ask(client, "muda a aula de BD para as 10h")  # imported: the next sync would undo it
    assert "Não sei" in r["reply"]
    assert "Para quando" in _ask(client, "muda a reunião para depois")["reply"] or _ask(client, "muda a reunião para depois")["intent"] != "change_item"


def test_ambiguous_references_ask_which(client, session):
    for title in ("Reunião com a Ana", "Reunião de grupo"):
        session.add(Event(title=title, kind="appointment", starts_at=NOW + timedelta(days=1), ends_at=NOW + timedelta(days=1, hours=1)))
    session.commit()
    r = _ask(client, "apaga a reunião")
    assert r["intent"] == "delete_item" and r["reply"].startswith("Qual?") and session.query(Event).count() == 2
    assert _ask(client, "apaga a reunião com a Ana")["intent"] == "confirm"
    assert _ask(client, "ok, mas espera")["intent"] != "delete_item" and session.query(Event).count() == 2


def test_tasks_are_completed_moved_and_deleted_by_reference(client, session):
    _ask(client, "adiciona uma tarefa: relatório de redes até sexta")
    assert _ask(client, "adia-a para terça")["intent"] == "change_item"
    session.expire_all()
    task = session.query(Task).one()
    assert _local(task.due_at) == "27/10 23:59"
    assert _ask(client, "já fiz o relatório")["intent"] == "complete_task"
    session.expire_all()
    assert session.query(Task).one().status == "COMPLETED"
    _ask(client, "desfaz")
    session.expire_all()
    assert session.query(Task).one().status == "TODO"
    assert _ask(client, "apaga a tarefa que criei")["intent"] == "confirm"
    _ask(client, "sim")
    session.expire_all()
    assert session.query(Task).count() == 0


def test_undo_of_a_creation_removes_it_and_nothing_to_undo_says_so(client, session):
    assert "Não tenho nada" in _ask(client, "desfaz")["reply"]
    _ask(client, "adiciona uma tarefa: ler o capítulo 3")
    _ask(client, "desfaz isso")
    assert session.query(Task).count() == 0


def test_it_expires_and_other_phrases_are_not_taken_for_changes(client, session):
    _ask(client, "marca uma reunião amanhã às 20h")
    later = NOW + assistant.CONTEXT_FOR + timedelta(minutes=1)
    app.dependency_overrides[get_now] = lambda: later
    assert "Não sei a que te referes" in _ask(client, "passa-a para as 22h")["reply"]
    for text, intent in [("muda para a área de trabalho 3", "switch_workspace"), ("passa a sessão para amanhã", "postpone_session"),
                         ("acabei de chegar", "help"), ("cancela", "cancel_desktop")]:
        assert assistant.match_rules(text, TODAY)[0] == intent, text


def test_recommended_files_can_be_listed_and_opened_again(client, session, data, monkeypatch):
    monkeypatch.setattr(assistant, "desktop_connected", lambda s, n: True)
    session.add(Material(course_id=data.course_id, section="T", title="Slides ER", kind="file", filename="er.pdf", source="moodle",
                         external_id="m1", vault_path="BD/slides.pdf"))
    session.delete(data)  # no open task: the course's recent files are recommended
    session.commit()
    assert _ask(client, "materiais de bases de dados")["items"]
    r = _ask(client, "quais foram os materiais que recomendaste?")
    assert r["intent"] == "open_recent" and r["items"][0]["vault_path"] == "BD/slides.pdf"
    r = _ask(client, "abre esses ficheiros")
    assert r["job"] and r["job"]["steps"] >= 1


def test_close_those_tabs_means_the_tabs_last_searched(client, session, monkeypatch):
    monkeypatch.setattr(assistant, "desktop_connected", lambda s, n: True)
    _ask(client, "procura os separadores do youtube")
    from peace import desktop
    desktop._put(session, "desktop_job", {})
    session.commit()
    r = _ask(client, "fecha esses separadores")
    assert r["intent"] == "confirm" and "youtube" in r["reply"]

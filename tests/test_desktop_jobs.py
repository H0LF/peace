"""Desktop actions through the assistant (peace.desktop + peace.assistant): plans from rules or the model,
confirmation before anything that can lose work, one claim per plan, verified summaries, partial failure,
cancellation and expiry. The agent side is played by the test, so no desktop is touched."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from peace import assistant, desktop
from peace.api import app
from peace.models import AssistantMessage, Course, Material, Task
from peace.routes import get_now

NOW = datetime(2026, 10, 26, 13, 0, tzinfo=UTC)
spec = importlib.util.spec_from_file_location("peace_actions", Path(__file__).parents[1] / "desktop" / "peace_actions.py")
agent_actions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_actions)


@pytest.fixture(autouse=True)
def fixed_now_and_no_model(monkeypatch):
    app.dependency_overrides[get_now] = lambda: NOW
    monkeypatch.setattr(assistant, "llm_reachable", lambda: False)
    yield
    app.dependency_overrides.pop(get_now, None)


def at(minutes: float) -> None:
    app.dependency_overrides[get_now] = lambda: NOW + timedelta(minutes=minutes)


@pytest.fixture
def connected(client):
    client.post("/api/v1/desktop/heartbeat", json={"capabilities": {"actions": {"categories": ["files", "windows"], "hyprland": True}}})


@pytest.fixture
def course(session):
    so = Course(name="Sistemas Operativos", code="2001", url="https://moodle.example/course/view.php?id=42")
    session.add(so)
    session.flush()
    session.add_all([
        Task(title="Ficha 2 - escalonamento de processos", course_id=so.id, due_at=NOW + timedelta(days=5), estimated_minutes=90),
        Material(course_id=so.id, section="Teóricas", title="Escalonamento", kind="file", filename="escalonamento.pdf",
                 vault_path="Cadeiras/SO/escalonamento.pdf", modified_at=NOW, content="escalonamento de processos round robin prioridades"),
    ])
    session.commit()
    return so


def ask(client, text):
    return client.post("/api/v1/assistant/ask", json={"text": text}).json()


def finish(client, job_id, results, **flags):
    return client.post(f"/api/v1/desktop/jobs/{job_id}/progress", json={"results": results, "final": True, **flags})


def ok(tool, message, verified=True):
    return {"tool": tool, "ok": True, "verified": verified, "code": None, "message": message}


def failed(tool, code, message):
    return {"tool": tool, "ok": False, "verified": False, "code": code, "message": message}


@pytest.mark.parametrize(("text", "intent", "args"), [
    ("Peace, vamos estudar Sistemas Operativos", "study_workspace", {"course": "sistemas operativos"}),
    ("quero estudar BD", "study_workspace", {"course": "bd"}),
    ("quero estudar agora", "study_now", {}),
    ("abre o obsidian", "open_app", {"app": "obsidian"}),
    ("Abre os materiais de bases de dados", "materials", {"course": "bases de dados"}),
    ("muda para a área de trabalho 3", "switch_workspace", {"workspace": 3}),
    ("arruma as janelas para programar", "apply_layout", {"name": "programacao"}),
    ("aplica a disposição de estudo", "apply_layout", {"name": "estudo"}),
    ("desfaz a disposição", "undo_layout", {}),
    ("guarda a disposição notas", "save_layout", {"name": "notas"}),
    ("fecha os separadores do youtube", "close_tabs", {"query": "youtube"}),
    ("agrupa os separadores de redes", "group_tabs", {"query": "redes", "title": "redes"}),
    ("mostra o separador do moodle", "focus_tab", {"query": "moodle"}),
    ("cancela", "cancel_desktop", {}),
    ("fecha a janela do kitty", "close_window", {"window": "kitty"}),
    ("mostra a janela do obsidian", "focus_window", {"window": "obsidian"}),
    ("move a janela do brave para a área 4", "move_window", {"window": "brave", "workspace": 4}),
    ("organiza o dia", "plan_day", {}),
    ("cancela a sessão", "skip_session", {}),
])
def test_rules_understand_desktop_requests_without_the_model(text, intent, args):
    assert assistant.match_rules(text, NOW.date()) == (intent, args)


def test_backend_tools_are_tools_the_agent_accepts():
    for name, (tool, _, params) in desktop.DESKTOP.items():
        assert tool in agent_actions.TOOLS, name
        assert set(params) <= set(agent_actions.TOOLS[tool].params), name
    for tool in desktop.DESTRUCTIVE_TOOLS:
        assert agent_actions.TOOLS[tool].destructive({}), tool


def test_nothing_is_planned_while_the_desktop_agent_is_away(client, session):
    r = ask(client, "abre o obsidian")
    assert r["job"] is None and "desligado" in r["reply"]
    assert desktop._pref(session, "desktop_job") == {}


def test_study_workspace_plans_layout_course_files_and_the_course_page(client, session, connected, course):
    r = ask(client, "Peace, vamos estudar Sistemas Operativos")
    assert r["intent"] == "study_workspace" and r["job"]["state"] == "pending" and r["to"] == f"/cadeiras/{course.id}"
    assert [i["vault_path"] for i in r["items"]] == ["Cadeiras/SO/escalonamento.pdf"]
    claimed = client.get("/api/v1/desktop/jobs/next").json()
    assert claimed["steps"] == [
        {"tool": "layouts.apply", "args": {"name": "estudo"}},
        {"tool": "files.open", "args": {"root": "vault", "path": "Cadeiras/SO/escalonamento.pdf"}},
        {"tool": "browser.open", "args": {"url": "https://moodle.example/course/view.php?id=42", "group": "SO"}},
    ]
    assert claimed["confirmed"] is False and client.get("/api/v1/desktop/jobs/next").json() is None  # claimed once
    finish(client, claimed["id"], [ok("layouts.apply", "Disposição «estudo»: 2 de 2 janelas na área 5."),
                                   ok("files.open", "Pedi para abrir «escalonamento.pdf», mas não confirmei a janela.", verified=False),
                                   failed("browser.open", "browser_unavailable", "A extensão PEACE do Brave não está ligada.")])
    job = client.get(f"/api/v1/desktop/jobs/{claimed['id']}").json()
    assert job["state"] == "partial"
    assert job["summary"] == ("Fiz 2 de 3 passos. Disposição «estudo»: 2 de 2 janelas na área 5. Pedi para abrir «escalonamento.pdf», "
                              "mas não confirmei a janela. Falhou: A extensão PEACE do Brave não está ligada.")
    assert session.query(AssistantMessage).filter_by(intent="desktop_result").one().content == job["summary"]


def test_an_unknown_course_plans_nothing(client, session, connected, course):
    r = ask(client, "vamos estudar astrofísica")
    assert r["job"] is None and r["reply"] == "Não encontrei a cadeira «astrofisica»."


def test_summaries_never_claim_more_than_the_agent_verified(client, connected):
    job = ask(client, "abre o obsidian")["job"]
    claimed = client.get("/api/v1/desktop/jobs/next").json()
    assert claimed["steps"] == [{"tool": "apps.launch", "args": {"app": "obsidian"}}]
    finish(client, job["id"], [ok("apps.launch", "Pedi para abrir Obsidian, mas não vi a janela aparecer.", verified=False)])
    status = client.get(f"/api/v1/desktop/jobs/{job['id']}").json()
    assert status["state"] == "done" and status["summary"].startswith("Feito, mas há passos que não consegui confirmar.")


def test_closing_tabs_waits_for_a_yes_and_runs_confirmed(client, connected):
    r = ask(client, "fecha os separadores do youtube")
    assert r["intent"] == "confirm" and r["job"]["state"] == "confirm" and "Responde «sim»" in r["reply"]
    assert client.get("/api/v1/desktop/jobs/next").json() is None
    r = ask(client, "sim")
    assert r["via"] == "confirmed" and r["job"]["state"] == "pending"
    claimed = client.get("/api/v1/desktop/jobs/next").json()
    assert claimed["confirmed"] is True and claimed["steps"] == [{"tool": "browser.close", "args": {"query": "youtube"}}]


def test_a_late_yes_does_nothing(client, connected):
    ask(client, "fecha os separadores do youtube")
    at(3)
    assert ask(client, "sim")["intent"] == "help"
    assert client.get("/api/v1/desktop/jobs/next").json() is None


def test_steps_the_agent_refused_without_confirmation_can_be_confirmed(client, connected):
    job = ask(client, "guarda a disposição notas")["job"]
    client.get("/api/v1/desktop/jobs/next")
    finish(client, job["id"], [failed("layouts.save", "confirmation_required", "Esta ação precisa da tua confirmação.")])
    status = client.get(f"/api/v1/desktop/jobs/{job['id']}").json()
    assert status["state"] == "failed" and status["summary"].endswith("Responde «sim».")
    assert ask(client, "sim")["job"]["state"] == "pending"
    claimed = client.get("/api/v1/desktop/jobs/next").json()
    assert claimed["confirmed"] and claimed["steps"] == [{"tool": "layouts.save", "args": {"name": "notas"}}]


def test_model_multi_step_plans_keep_only_approved_desktop_tools(client, connected, monkeypatch):
    calls = [{"function": {"name": "switch_workspace", "arguments": {"workspace": "4"}}},
             {"function": {"name": "open_app", "arguments": {"app": "kitty", "command": "rm -rf ~"}}},
             {"function": {"name": "run_shell", "arguments": {"cmd": "rm -rf ~"}}},
             {"function": {"name": "deadlines", "arguments": {}}},
             {"function": {"name": "focus_window", "arguments": '{"window": "kitty"}'}}]
    monkeypatch.setattr(assistant, "llm_reachable", lambda: True)
    monkeypatch.setattr(assistant, "_llm", lambda *a, **kw: type("R", (), {"raise_for_status": lambda self: None,
                                                                            "json": lambda self: {"message": {"tool_calls": calls}}})())
    r = ask(client, "prepara-me o terminal na área 4")
    assert r["intent"] == "confirm" and r["job"] is None  # only the model heard a PC action: ask first
    assert r["reply"] == "Confirmas: mudar para a área de trabalho 4; abrir kitty; pôr kitty em foco? Responde «sim»."
    r = ask(client, "sim")
    assert r["via"] == "confirmed" and r["job"]["steps"] == 3
    assert client.get("/api/v1/desktop/jobs/next").json()["steps"] == [
        {"tool": "workspaces.switch", "args": {"workspace": 4}}, {"tool": "apps.launch", "args": {"app": "kitty"}},
        {"tool": "windows.focus", "args": {"window": "kitty"}}]


def test_the_model_offers_desktop_tools_alongside_the_academic_ones():
    names = [t["function"]["name"] for t in assistant._tools()]
    assert {"study_workspace", "open_app", "close_tabs", "apply_layout", "day"} <= set(names) and "desktop_confirm" not in names


def test_one_plan_at_a_time_and_cancellation(client, connected):
    first = ask(client, "abre o obsidian")["job"]
    assert "Já há uma ação" in ask(client, "abre o kitty")["reply"]
    client.get("/api/v1/desktop/jobs/next")
    assert ask(client, "cancela")["intent"] == "cancel_desktop"
    r = client.post(f"/api/v1/desktop/jobs/{first['id']}/progress", json={"results": []})
    assert r.json() == {"cancel": True}
    finish(client, first["id"], [], cancelled=True, final=False)
    status = client.get(f"/api/v1/desktop/jobs/{first['id']}").json()
    assert status["state"] == "cancelled" and status["summary"] == "Nada foi feito. Cancelado a pedido."
    second = ask(client, "abre o kitty")["job"]
    assert client.post(f"/api/v1/desktop/jobs/{second['id']}/cancel").json()["state"] == "cancelled"
    assert client.get("/api/v1/desktop/jobs/next").json() is None


def test_unclaimed_and_abandoned_plans_are_reported_not_hidden(client, connected):
    job = ask(client, "abre o obsidian")["job"]
    at(1)
    status = client.get(f"/api/v1/desktop/jobs/{job['id']}").json()
    assert status["state"] == "expired" and "não pegou" in status["summary"]
    assert client.get("/api/v1/desktop/jobs/next").json() is None
    client.post("/api/v1/desktop/heartbeat", json={})
    job = ask(client, "abre o kitty")["job"]
    client.get("/api/v1/desktop/jobs/next")
    client.post(f"/api/v1/desktop/jobs/{job['id']}/progress", json={"results": [ok("apps.launch", "Abri kitty.")]})
    at(5)
    status = client.get(f"/api/v1/desktop/jobs/{job['id']}").json()
    assert status["state"] == "failed" and status["summary"] == "Feito. Abri kitty. O agente deixou de responder a meio."
    assert finish(client, job["id"], []).status_code == 409


def test_results_are_validated_and_trimmed(client, connected):
    job = ask(client, "procura os separadores do moodle")["job"]
    client.get("/api/v1/desktop/jobs/next")
    bad = finish(client, job["id"], [{"tool": "browser.tabs", "ok": "yes", "verified": True, "message": "x"}])
    assert bad.status_code == 422
    items = [{"title": "t" * 500, "url": "https://x"}] * 30
    finish(client, job["id"], [ok("browser.tabs", "30 separador(es).") | {"data": {"items": items, "secret": "x"}}])
    stored = client.get(f"/api/v1/desktop/jobs/{job['id']}").json()["results"][0]["data"]
    assert list(stored) == ["items"] and len(stored["items"]) == 20 and len(stored["items"][0]["title"]) == 200


def test_status_shows_what_the_desktop_can_do(client, connected):
    assert client.get("/api/v1/assistant/status").json()["desktop"]["actions"] == {"categories": ["files", "windows"],
                                                                                    "hyprland": True, "browser": False}
    assert client.post("/api/v1/desktop/heartbeat", json={"capabilities": {"actions": {"categories": ["shell"]}}}).status_code == 422



def test_a_question_is_never_turned_into_a_pc_action_by_the_model(client, connected, monkeypatch):
    # Seen live: the model repeated an earlier "muda para a área 3" for «O que tenho amanhã?».
    monkeypatch.setattr(assistant, "llm_reachable", lambda: True)
    calls = [{"function": {"name": "switch_workspace", "arguments": {"workspace": 3}}}]
    monkeypatch.setattr(assistant, "_llm", lambda *a, **kw: type("R", (), {"raise_for_status": lambda self: None,
                                                                            "json": lambda self: {"message": {"tool_calls": calls}}})())
    r = ask(client, "O que tenho amanhã?")
    assert r["intent"] == "day" and r["job"] is None and r["via"] == "rules"
    assert client.get("/api/v1/desktop/jobs/next").json() is None


def test_pc_actions_the_rules_also_heard_run_without_asking(client, connected, monkeypatch):
    monkeypatch.setattr(assistant, "llm_reachable", lambda: True)
    calls = [{"function": {"name": "switch_workspace", "arguments": {"workspace": 3}}}]
    monkeypatch.setattr(assistant, "_llm", lambda *a, **kw: type("R", (), {"raise_for_status": lambda self: None,
                                                                            "json": lambda self: {"message": {"tool_calls": calls}}})())
    r = ask(client, "muda para a área de trabalho 3")
    assert r["via"] == "model" and r["job"]["state"] == "pending"

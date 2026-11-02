"""Assignment requirements (phase 5): reading the brief, revisions as Proposals, the Sandbox, readiness, export."""

import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from peace import requirements, sandbox
from peace.api import app
from peace.assistant import cmd_readiness, match_rules
from peace.models import Material, Proposal, Requirement, Task
from peace.routes import get_now

API = "/api/v1"
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
BRIEF = ("[p. 1] Trabalho Prático de Bases de Dados\n\nO programa deve validar o NIF de cada cliente.\n\n"
         "[p. 2] A entrega é feita no Moodle num ficheiro ZIP com o relatório em PDF.\n\n"
         "Não é permitido usar ORMs.\n\nA defesa vale 30% da nota.")
needs_bwrap = pytest.mark.skipif(not sandbox.available(), reason="bubblewrap not installed")


@pytest.fixture
def model(monkeypatch, tmp_path):
    """The model's answers, in order, one list per call; it is reachable. PEACE's data folder is temporary."""
    answers: list[list[dict]] = []
    calls: list[str] = []

    def ask(system, user, schema, timeout=180):
        calls.append(user)
        return {"items": answers.pop(0)} if answers else {"items": []}
    monkeypatch.setattr(requirements, "ask_json", ask)
    monkeypatch.setattr(requirements, "llm_reachable", lambda: True)
    monkeypatch.setenv("PEACE_DATA_DIR", str(tmp_path / "data"))
    app.dependency_overrides[get_now] = lambda: NOW
    yield answers, calls
    app.dependency_overrides.pop(get_now, None)


def item(kind, text, quote):
    return {"kind": kind, "text": text, "quote": quote}


def assignment(client, session, content=BRIEF):
    course = client.post(f"{API}/courses", json={"name": "Bases de Dados", "code": "BD"}).json()["id"]
    brief = Material(course_id=course, section="Trabalho", title="Enunciado", kind="file", filename="TP_BD.pdf",
                     content=content, modified_at=NOW - timedelta(days=10), source="moodle", external_id=f"file:tp:{course}")
    task = Task(title="Trabalho prático de BD", course_id=course, kind="assignment", due_at=NOW + timedelta(days=5))
    session.add_all([brief, task])
    session.commit()
    client.put(f"{API}/tasks/{task.id}/brief", json={"material_id": brief.id})
    return task, brief


FIRST = [item("functional", "Validar o NIF de cada cliente", "O programa deve validar o NIF de cada cliente."),
         item("deliverable", "Entregar ZIP com relatório em PDF", "A entrega é feita no Moodle num ficheiro ZIP com o relatório em PDF."),
         item("constraint", "Não usar ORMs", "Não é permitido usar ORMs."),
         item("grading", "Inventado", "A apresentação vale 50%."),  # not in the brief: dropped
         item("functional", "Validar o NIF de cada cliente", "O programa deve validar o NIF de cada cliente.")]  # twice


def test_first_read_fills_the_checklist_with_verbatim_quotes_and_pages(client, session, model):
    answers, calls = model
    task, brief = assignment(client, session)
    answers.append(FIRST)
    assert client.post(f"{API}/tasks/{task.id}/requirements/read").json() == {"state": "reading"}
    page = client.get(f"{API}/tasks/{task.id}/delivery").json()
    assert page["reading"]["state"] == "done" and page["brief"]["filename"] == "TP_BD.pdf"
    reqs = page["requirements"]
    assert [(r["kind"], r["text"]) for r in reqs] == [("functional", "Validar o NIF de cada cliente"),
                                                      ("deliverable", "Entregar ZIP com relatório em PDF"), ("constraint", "Não usar ORMs")]
    assert [r["evidence"]["page"] for r in reqs] == [1, 2, 2] and reqs[0]["evidence"]["file"] == "TP_BD.pdf"
    assert all(r["state"] == "unverified" and r["origin"] == "model" for r in reqs)
    assert "Trabalho prático de BD" in calls[0] and "validar o NIF" in calls[0]  # the brief goes in as data


def test_pages_and_quotes_across_page_breaks():
    text = "[p. 1] Intro\nO programa deve\n[p. 2] validar o NIF.\nOutra linha.\n[p. 3] Entrega em ZIP."
    assert requirements.page_of(text, "Outra linha.") == 2
    assert requirements.page_of(text, "O programa deve validar o NIF.") == 1  # starts on page 1, ends on page 2
    assert requirements.page_of(text, "Entrega em ZIP.") == 3 and requirements.page_of(text, "não está") is None
    assert requirements.page_of("sem marcas de página", "sem marcas") is None
    long = "\n".join(f"Linha {i} " + "x" * 90 for i in range(200))  # no blank lines, as PDF text comes
    pieces = requirements.chunks(long)
    assert all(len(p) <= requirements.CHUNK for p in pieces) and "".join(pieces) == long
    assert all(p.endswith("\n") for p in pieces[:-1])  # cut at line ends, never mid-line
    one_line = " ".join(f"Frase {i} sobre o trabalho." for i in range(600))  # a whole page as one line
    pieces = requirements.chunks(one_line)
    assert "".join(pieces) == one_line and all(p.endswith(". ") for p in pieces[:-1])  # after a sentence


def test_the_same_requirement_in_other_words_is_one():
    """Pairs from a real brief (BD 2026/27): the first two say the same thing, the others do not."""
    a = "Entregar o Modelo Conceptual E/R (notação Chen) em PDF"
    same = ["Modelo Conceptual E/R em PDF", "PDF do diagrama do Modelo Conceptual E/R"]
    other = ["Modelo Físico E/R em PowerDesigner", "Entregar o Modelo Conceptual E/R corrigido e o Modelo Físico E/R em PowerDesigner"]
    assert all(requirements.containment(a, b) >= requirements.DUPLICATE for b in same)
    assert all(requirements.containment(a, b) < requirements.DUPLICATE for b in other)


def test_a_revised_brief_becomes_one_proposal_and_keeps_states(client, session, model):
    answers, _ = model
    task, brief = assignment(client, session)
    answers.append(FIRST)
    client.post(f"{API}/tasks/{task.id}/requirements/read")
    reqs = {r.text: r for r in session.query(Requirement).filter_by(task_id=task.id)}
    client.patch(f"{API}/requirements/{reqs['Validar o NIF de cada cliente'].id}", json={"state": "verified"})
    client.patch(f"{API}/requirements/{reqs['Não usar ORMs'].id}", json={"text": "Não usar ORMs (nem Hibernate)"})
    client.post(f"{API}/tasks/{task.id}/requirements", json={"kind": "deliverable", "text": "Pôr os nomes no relatório"})

    brief.content = BRIEF.replace("ficheiro ZIP com o relatório em PDF", "ficheiro ZIP com o relatório e o código") + "\n\nO programa deve exportar CSV."
    brief.modified_at = NOW - timedelta(days=1)
    session.commit()
    assert client.get(f"{API}/tasks/{task.id}/delivery").json()["brief_changed"]
    answers.append([FIRST[0], item("deliverable", "Entregar ZIP com relatório e código", "A entrega é feita no Moodle num ficheiro ZIP com o relatório e o código."),
                    item("functional", "Exportar CSV", "O programa deve exportar CSV.")])  # "Não usar ORMs" no longer read
    client.post(f"{API}/tasks/{task.id}/requirements/read")
    p = session.query(Proposal).filter_by(kind="requirements").one()
    assert p.title == "O enunciado de «Trabalho prático de BD» mudou"
    d = p.evidence[0]["diff"]
    assert [a["text"] for a in d["added"]] == ["Exportar CSV"]
    assert [c["before"] for c in d["changed"]] == ["Entregar ZIP com relatório em PDF"]
    assert d["removed"] == []  # the student edited "Não usar ORMs": never removed by a re-read
    assert session.query(Requirement).filter_by(task_id=task.id).count() == 4  # nothing changes until applied

    assert client.post(f"{API}/proposals/{p.id}/apply").json()["status"] == "applied"
    session.expire_all()
    now = {r.text: r for r in session.query(Requirement).filter_by(task_id=task.id)}
    assert now["Validar o NIF de cada cliente"].state == "verified"  # unchanged keeps its state
    assert {"Exportar CSV", "Entregar ZIP com relatório e código", "Não usar ORMs (nem Hibernate)", "Pôr os nomes no relatório"} <= set(now)
    assert not client.get(f"{API}/tasks/{task.id}/delivery").json()["brief_changed"]
    client.post(f"{API}/proposals/{p.id}/undo")
    session.expire_all()
    assert {r.text for r in session.query(Requirement).filter_by(task_id=task.id)} == {
        "Validar o NIF de cada cliente", "Entregar ZIP com relatório em PDF", "Não usar ORMs (nem Hibernate)", "Pôr os nomes no relatório"}


def test_reading_needs_a_brief_and_the_model(client, session, model, monkeypatch):
    task, _ = assignment(client, session)
    client.put(f"{API}/tasks/{task.id}/brief", json={"material_id": None})
    assert client.post(f"{API}/tasks/{task.id}/requirements/read").status_code == 409
    task, _ = assignment(client, session)
    monkeypatch.setattr(requirements, "ask_json", lambda *a, **k: None)
    client.post(f"{API}/tasks/{task.id}/requirements/read")
    assert "não respondeu" in client.get(f"{API}/tasks/{task.id}/delivery").json()["reading"]["error"]


def test_the_project_folder_must_be_the_students_own(client, session, model, tmp_path, monkeypatch):
    task, _ = assignment(client, session)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "proj").mkdir()
    (tmp_path / ".ssh").mkdir()
    for bad in ("/etc", str(tmp_path / ".ssh"), str(tmp_path), "relativo", str(tmp_path / "nao-existe")):
        assert client.put(f"{API}/tasks/{task.id}/project", json={"path": bad}).status_code == 422, bad
    assert client.put(f"{API}/tasks/{task.id}/project", json={"path": "~/proj"}).json()["project"] == str(tmp_path / "proj")


@needs_bwrap
def test_checks_run_sandboxed_and_verify_their_requirement(client, session, model, tmp_path, monkeypatch):
    answers, _ = model
    task, _ = assignment(client, session)
    answers.append(FIRST)
    client.post(f"{API}/tasks/{task.id}/requirements/read")
    req = session.query(Requirement).filter_by(task_id=task.id, kind="functional").one()
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / "trabalho"
    proj.mkdir()
    (proj / "main.py").write_text("print('ok')\n")
    client.put(f"{API}/tasks/{task.id}/project", json={"path": str(proj)})

    ok = client.post(f"{API}/tasks/{task.id}/checks", json={"name": "Corre", "command": "python3 main.py && echo extra > novo.txt && rm main.py",
                                                         "requirement_id": req.id}).json()
    ran = client.post(f"{API}/checks/{ok['id']}/run").json()
    assert ran["last_status"] == "passed" and "ok" in ran["last_output"]
    assert (proj / "main.py").exists() and not (proj / "novo.txt").exists()  # the project is never changed
    session.expire_all()
    assert (session.get(Requirement, req.id).state, session.get(Requirement, req.id).verified_by) == ("verified", f"check:{ok['id']}")

    net = client.post(f"{API}/tasks/{task.id}/checks", json={"name": "Rede", "command":
                      "python3 -c \"import socket; socket.create_connection(('example.com', 53), 2)\""}).json()
    assert client.post(f"{API}/checks/{net['id']}/run").json()["last_status"] == "failed"  # no network
    home = client.post(f"{API}/tasks/{task.id}/checks", json={"name": "Casa", "command": "ls /home"}).json()
    assert client.post(f"{API}/checks/{home['id']}/run").json()["last_status"] == "failed"  # the student's files are not there

    (proj / "main.py").write_text("raise SystemExit(1)\n")
    assert client.post(f"{API}/checks/{ok['id']}/run").json()["last_status"] == "failed"
    session.expire_all()
    assert session.get(Requirement, req.id).state == "unverified"  # it passed before and fails now


@needs_bwrap
def test_git_is_read_in_the_sandbox_and_readiness_is_exported(client, session, model, tmp_path, monkeypatch):
    answers, _ = model
    task, _ = assignment(client, session)
    answers.append(FIRST[:2])
    client.post(f"{API}/tasks/{task.id}/requirements/read")
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / "repo"
    proj.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(proj)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    (proj / "a.txt").write_text("1")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "primeiro"], check=True)
    (proj / "b.txt").write_text("2")
    client.put(f"{API}/tasks/{task.id}/project", json={"path": str(proj)})

    r = client.get(f"{API}/tasks/{task.id}/delivery").json()["readiness"]
    assert r["git"]["branch"] == "main" and r["git"]["changed"] == 1 and "primeiro" in r["git"]["commits"][0]
    assert not r["ready"] and "0 de 2 requisitos verificados" in r["summary"] and "1 ficheiros por guardar" in r["summary"]
    for q in session.query(Requirement).filter_by(task_id=task.id):
        client.patch(f"{API}/requirements/{q.id}", json={"state": "verified"})
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "segundo"], check=True)
    r = client.get(f"{API}/tasks/{task.id}/delivery").json()["readiness"]
    assert r["ready"] and "Tudo verificado" in r["summary"]

    path = client.post(f"{API}/tasks/{task.id}/readiness/export").json()["path"]
    assert path == "Bases de Dados/Trabalho prático de BD.md"
    note = (requirements.deliveries_dir() / path).read_text()
    assert "- [x] Validar o NIF de cada cliente *(p. 1)*" in note and "nunca entrega" in note
    assert client.get(f"{API}/knowledge/deliveries").json()[0]["path"] == path


def test_assistant_answers_readiness(client, session, model):
    answers, _ = model
    task, _ = assignment(client, session)
    answers.append(FIRST[:1])
    client.post(f"{API}/tasks/{task.id}/requirements/read")
    req = session.query(Requirement).filter_by(task_id=task.id).one()
    client.patch(f"{API}/requirements/{req.id}", json={"state": "missing"})
    session.expire_all()  # the change came through the API's own session
    assert match_rules("estou pronto para entregar o trabalho prático de BD?", NOW.date())[0] == "readiness"
    assert match_rules("o que falta no trabalho prático de BD", NOW.date())[0] == "readiness"
    reply = cmd_readiness(session, NOW, text="estou pronto para entregar o trabalho prático de BD?")
    assert reply.to == f"/entregas/{task.id}" and "Em falta: Validar o NIF de cada cliente." in reply.text

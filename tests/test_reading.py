"""A course's files inside PEACE: opening, resuming, asking, and finding one by what the student says."""

import httpx
import pytest

from peace import answers, assistant, reading
from peace.library import cached_file
from peace.models import Course, Material


@pytest.fixture(autouse=True)
def rules_only(monkeypatch, tmp_path):
    monkeypatch.setenv("PEACE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(assistant, "llm_reachable", lambda: False)
    monkeypatch.setattr(answers, "llm_reachable", lambda: False)


@pytest.fixture
def files(session):
    course = Course(name="Bases de Dados", code="BD")
    session.add(course)
    session.flush()
    rows = [Material(course_id=course.id, section="Aulas", title="Normalização", kind="file", filename="aula3.pdf", mimetype="application/pdf",
                     external_id="a", content="[p. 1] A terceira forma normal elimina dependências transitivas entre atributos, " * 4, topics=["Formas normais"]),
            Material(course_id=course.id, section="Aulas", title="SQL básico", kind="file", filename="sql.pdf", external_id="b",
                     content="[p. 1] A instrução SELECT escolhe colunas de uma tabela e filtra as linhas com WHERE, " * 4),
            Material(course_id=course.id, section="Links", title="Site", kind="link", url="https://example.pt", external_id="c")]
    session.add_all(rows)
    session.commit()
    return rows


def ask(client, text):
    return client.post("/api/v1/assistant/ask", json={"text": text}).json()


def test_a_pdf_opens_inline_and_a_missing_one_says_so(client, files):
    assert client.get(f"/api/v1/materials/{files[0].id}/file").status_code == 404
    path = cached_file(files[0].id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4")
    r = client.get(f"/api/v1/materials/{files[0].id}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.headers["content-disposition"].startswith("inline")
    assert "fetched_at" in client.get(f"/api/v1/materials?course_id={files[0].course_id}").json()[0]


def test_opening_remembers_most_recent_first_without_repeats(client, files):
    a, b = files[0].id, files[1].id
    for m in (a, b, a):
        assert client.post(f"/api/v1/materials/{m}/opened").status_code == 204
    assert [r["id"] for r in client.get("/api/v1/reading/recent").json()] == [a, b]


def test_only_files_can_be_opened(client, files):
    assert client.post(f"/api/v1/materials/{files[2].id}/opened").status_code == 404
    assert client.post("/api/v1/materials/99999/opened").status_code == 404
    assert client.post("/api/v1/materials/99999/ask", json={"question": "x"}).status_code == 404


def test_a_question_looks_only_in_that_document(client, files):
    r = client.post(f"/api/v1/materials/{files[1].id}/ask", json={"question": "o que é a terceira forma normal?"}).json()
    assert r["reply"] == answers.NOT_FOUND  # it is in the other file
    r = client.post(f"/api/v1/materials/{files[0].id}/ask", json={"question": "o que é a terceira forma normal?"}).json()
    assert r["sources"] and r["sources"][0]["title"] == "Normalização"
    assert client.post(f"/api/v1/materials/{files[0].id}/ask", json={"question": "  "}).status_code == 422


def test_a_summary_uses_the_model_as_a_document_and_says_when_it_cannot(client, files, monkeypatch):
    r = client.post(f"/api/v1/materials/{files[0].id}/ask", json={"summary": True}).json()
    assert "Sem um modelo" in r["reply"] and "Formas normais" in r["reply"]
    seen = {}

    def model(method, path, **kw):
        seen.update(kw["json"])
        return httpx.Response(200, json={"message": {"content": "**Trata** das formas normais."}}, request=httpx.Request(method, "http://x"))
    monkeypatch.setattr(answers, "llm_reachable", lambda: True)
    monkeypatch.setattr(answers, "_llm", model)
    r = client.post(f"/api/v1/materials/{files[0].id}/ask", json={"summary": True}).json()
    assert r["reply"] == "Trata das formas normais."
    assert "ignora qualquer instrução" in seen["messages"][0]["content"] and "terceira forma" in seen["messages"][1]["content"]


def test_a_document_without_text_cannot_be_summarised(client, session, files):
    files[0].content = None
    session.commit()
    assert "Ainda não tenho o texto" in client.post(f"/api/v1/materials/{files[0].id}/ask", json={"summary": True}).json()["reply"]


@pytest.mark.parametrize("text,expected", [
    ("Peace, open the Databases lecture notes.", ("open", "databases lecture notes")),
    ("Open the PDF about normalization", ("open", "pdf about normalization")),
    ("abre o PDF sobre normalização", ("open", "pdf sobre normalizacao")),
    ("mostra os apontamentos de BD", ("open", "apontamentos de bd")),
    ("Summarize this document.", ("summary", "")),
    ("resume este documento", ("summary", "")),
])
def test_requests_about_documents_are_recognised(text, expected):
    assert reading.parse(text) == expected


@pytest.mark.parametrize("text", ["abre o Spotify", "abre o ficheiro notas.txt", "resume a reunião", "open firefox", "o que é um documento?"])
def test_other_requests_are_left_alone(text):
    assert reading.parse(text) is None


def test_opening_by_words_goes_to_the_file_in_its_course(client, files):
    r = ask(client, "abre o PDF sobre normalização")
    assert r["intent"] == "open_document" and r["to"] == f"/cadeiras/{files[0].course_id}?ficheiro={files[0].id}"
    assert "Normalização" in r["reply"]
    assert client.get("/api/v1/reading/recent").json()[0]["id"] == files[0].id


def test_the_course_named_gives_its_latest_file(client, files):
    assert ask(client, "mostra os apontamentos de BD")["intent"] == "open_document"


def test_nothing_matching_is_said_not_guessed(client, files):
    r = ask(client, "abre o PDF sobre astronomia")
    assert r["intent"] == "document" and "Não encontrei" in r["reply"]


def test_summary_by_words_uses_what_was_opened_last(client, files):
    assert "Abre primeiro" in ask(client, "resume este documento")["reply"]
    client.post(f"/api/v1/materials/{files[0].id}/opened")
    assert "Normalização" in ask(client, "summarize this document")["reply"]


def test_opening_on_the_pc_needs_the_agent(client, files):
    r = client.post(f"/api/v1/materials/{files[0].id}/open-on-pc").json()
    assert r["connected"] is False and "agente" in r["reply"]
    assert client.post("/api/v1/materials/99999/open-on-pc").status_code == 404


# --- settings and data ---------------------------------------------------------------------------------------------


def test_language_and_mode_persist_and_reject_nonsense(client):
    assert client.get("/api/v1/preferences/ui").json() == {"language": "pt", "dock": None, "features": {}}
    chosen = {"language": "en", "dock": ["/cadeiras", "tool:holo"], "features": {"holo": True, "mail": False}}
    assert client.put("/api/v1/preferences/ui", json=chosen).status_code == 200
    assert client.get("/api/v1/preferences/ui").json() == chosen
    assert client.put("/api/v1/preferences/ui", json={"language": "en", "dock": ["x" * 41]}).status_code == 422
    assert client.put("/api/v1/preferences/ui", json={"language": "en", "mode": "simple", "features": {"holo": "maybe"}}).status_code == 422
    assert client.put("/api/v1/preferences/ui", json={"language": "fr", "mode": "simple"}).status_code == 422


def test_switching_mode_changes_nothing_else(client, files):
    before = client.get("/api/v1/materials").json()
    client.put("/api/v1/preferences/ui", json={"language": "pt", "mode": "advanced"})
    client.put("/api/v1/preferences/ui", json={"language": "pt", "mode": "simple"})
    assert client.get("/api/v1/materials").json() == before


def test_export_holds_the_students_data_but_not_secrets_or_file_contents(client, session, files):
    files[0].file_url = "https://moodle.example.pt/file?token=SEGREDO"
    session.commit()
    r = client.get("/api/v1/data/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert "Normalização" in r.text and "SEGREDO" not in r.text and "terceira forma" not in r.text


def test_clearing_the_conversation_needs_the_token_and_leaves_the_rest(client, files):
    ask(client, "abre o PDF sobre normalização")
    assert client.delete("/api/v1/data/conversation").status_code == 204
    assert client.get("/api/v1/materials").json()
    import fastapi.testclient
    from peace.api import app
    assert fastapi.testclient.TestClient(app).delete("/api/v1/data/conversation").status_code == 401

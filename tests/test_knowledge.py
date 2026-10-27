import hashlib
import importlib.util
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from peace import library, sync
from peace.knowledge import build_graph, communities, export_graphify, graph_json, load_index, suggest
from peace.library import extract_text, term_frequencies
from peace.models import Course, Event, Material, StudySession, Task
from peace.sources.inforestudante import parse_materials

NOW = datetime(2026, 10, 26, 13, 0, tzinfo=UTC)


# --- text and terms ------------------------------------------------------------------------------


def test_extracts_text_from_plain_and_office_files(tmp_path):
    (tmp_path / "a.cpp").write_text("int socket_fd = socket(AF_INET, SOCK_DGRAM, 0);")
    docx = tmp_path / "b.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", "<w:p><w:t>Modelo &amp; entidade</w:t></w:p>")
    pptx = tmp_path / "c.pptx"
    with zipfile.ZipFile(pptx, "w") as z:
        z.writestr("ppt/slides/slide10.xml", "<a:t>dez</a:t>")
        z.writestr("ppt/slides/slide2.xml", "<a:t>dois</a:t>")
    (tmp_path / "d.mp4").write_bytes(b"\x00\x01")
    assert "SOCK_DGRAM" in extract_text(tmp_path / "a.cpp", "a.cpp")
    assert extract_text(docx, "b.docx") == "Modelo & entidade"
    assert extract_text(pptx, "c.pptx") == "dois dez"  # natural slide order
    assert extract_text(tmp_path / "d.mp4", "d.mp4") == ""


def test_terms_drop_stopwords_and_keep_word_pairs():
    terms = term_frequencies("O protocolo UDP e o protocolo TCP. Protocolo UDP sem ligação. Protocolo UDP.")
    assert "protocolo udp" in terms and "protocolo" in terms
    assert not {"o", "e", "sem"} & set(terms)


# --- inforestudante materials --------------------------------------------------------------------

AJAX = """
<a class="handMouseCursor" onclick="x" id="link_seccao_0"> <span><img/></span> Materiais sem Módulo definido </a>
<table>
<tr id="divLinhaMat1336626" class="lightrow"><td><strong><a href="#">Enunciado TP</a></strong><script>notaVisivel(true);</script></td>
<td>Material de Avaliação</td><td><strong>14-10-2026</strong></td>
<td><input type="checkbox" value="0_1336626_3499905"/></td><td><a class="botaoDownload" href="downloadFichMatApoio.do?args=1">Download</a></td><td></td></tr>
<tr style="display:none" id="lista1336626"><td><table><tr><td>Descrição:</td><td></td></tr><tr><td>Tamanho:</td><td>321 KB</td></tr><tr><td>Extensão:</td><td>.pdf</td></tr></table></td></tr>
<tr id="divLinhaMat9" class="darkrow"><td>Só descrição</td><td>Outro</td><td>01-10-2026</td><td></td><td></td><td></td></tr>
</table>"""


def test_inforestudante_material_rows():
    (m,) = parse_materials(AJAX, "60000004")
    assert (m["external_id"], m["title"], m["section"], m["filename"], m["size"]) == (
        "60000004:1336626:3499905", "Enunciado TP", "Material de Avaliação", "Enunciado TP.pdf", 321 * 1024)
    assert m["modified_at"].date().isoformat() == "2026-10-14"


# --- relevance, graph, Graphify ------------------------------------------------------------------


@pytest.fixture
def library_data(session):
    bd, rc = Course(name="Bases de Dados", code="1"), Course(name="Redes", code="2")
    session.add_all([bd, rc])
    session.flush()
    texts = {
        (bd, "ER.pdf"): "modelo entidade relacionamento modelo ER entidades atributos modelo entidade relacionamento",
        (bd, "SQL.pdf"): "consultas SQL select join consultas SQL tabelas select join",
        (rc, "UDP.pdf"): "sockets UDP datagramas sockets UDP portas sockets",
        (rc, "TCP.pdf"): "sockets TCP ligação sockets TCP handshake sockets",
    }
    for (course, name), text in texts.items():
        session.add(Material(course_id=course.id, section="T", title=name, kind="file", filename=name, source="moodle",
                             external_id=name, vault_path=f"x/{name}", terms=term_frequencies(text), modified_at=NOW))
    task = Task(title="Meta3 - Modelo entidade relacionamento", course_id=bd.id)
    vague = Task(title="Teste 2", course_id=rc.id)
    session.add_all([task, vague, Event(title="RC (Teste 2)", kind="exam", course_id=rc.id,
                                         starts_at=NOW + timedelta(days=3), ends_at=NOW + timedelta(days=3, hours=2))])
    session.commit()
    return task, vague


def test_suggest_picks_the_matching_file_and_explains_why(session, library_data):
    task, vague = library_data
    index = load_index(session)
    first = suggest(index, task)[0]
    assert first["title"] == "ER.pdf" and first["why"].startswith("fala de ") and "entidade" in first["why"]
    assert all(s["title"] in ("ER.pdf", "SQL.pdf") for s in suggest(index, task))  # same course only
    assert suggest(index, vague) == []  # an unclassified recent file is not evidence of exam relevance


def test_graph_links_are_typed_and_inferred_ones_marked(session, library_data):
    G = build_graph(session, NOW)
    assert {"course", "material", "concept", "task", "exam"} <= {d["kind"] for _, d in G.nodes(data=True)}
    data = graph_json(G, communities(G))
    study = [link for link in data["links"] if link["relation"] == "study_with"]
    assert study and all(link["inferred"] for link in study)
    assert all(not link["inferred"] for link in data["links"] if link["relation"] == "contains")
    assert all(n["community"] is not None for n in data["nodes"])


def test_graphify_export_writes_graph_json_and_obsidian_notes(session, library_data, tmp_path, monkeypatch):
    monkeypatch.setenv("PEACE_DATA_DIR", str(tmp_path))
    G = build_graph(session, NOW)
    staging = export_graphify(G, communities(G))
    graph = json.loads((tmp_path / "graphify" / "graph.json").read_text())
    assert len(graph["nodes"]) == G.number_of_nodes()
    note = (staging / "ER.pdf.md").read_text()
    assert "[[" in note and "source_file" in note
    assert (staging / "PEACE.canvas").is_file()
    # Every node in the API names the note the export actually wrote.
    for node in graph_json(G, communities(G))["nodes"]:
        assert node["note"].startswith("07 - Generated/Graphify/")
        assert (staging / node["note"].removeprefix("07 - Generated/Graphify/")).is_file()


def test_fetch_pending_caches_and_analyzes_moodle_files(session, tmp_path, monkeypatch):
    monkeypatch.setenv("PEACE_DATA_DIR", str(tmp_path))
    m = Material(section="T", title="Notas", kind="file", filename="notas.txt", source="moodle", external_id="f1",
                 file_url="https://moodle.example.pt/moodle/webservice/pluginfile.php/1/notas.txt", modified_at=NOW)
    session.add(m)
    session.commit()

    class Fake:
        def iter_bytes(self):
            yield "sockets UDP e sockets TCP".encode()

        def close(self):
            pass

    monkeypatch.setattr(sync, "open_moodle_file", lambda url: (Fake(), Fake()))
    assert library.fetch_pending(session, NOW) == 1
    session.refresh(m)
    assert library.cached_file(m.id).read_text() == "sockets UDP e sockets TCP"
    assert "socket" in m.terms and m.fetched_modified_at == NOW and m.fetch_error is None
    assert library.fetch_pending(session, NOW) == 0  # up to date


def test_inforestudante_is_not_downloaded_automatically_or_marked_consulted(session, tmp_path, monkeypatch):
    monkeypatch.setenv("PEACE_DATA_DIR", str(tmp_path))
    m = Material(section="T", title="Sem data", kind="file", filename="a.txt", source="inforestudante",
                 external_id="1:2:3", modified_at=None)
    session.add(m)
    session.commit()
    assert library.pending(session, NOW) == []
    assert library.fetch_pending(session, NOW) == 0
    session.refresh(m)
    assert m.fetched_at is None
    assert not library.cached_file(m.id).exists()


def test_sessions_and_graph_api(client, session, library_data):
    task, _ = library_data
    session.add(StudySession(task_id=task.id, starts_at=NOW + timedelta(hours=1), ends_at=NOW + timedelta(hours=2)))
    session.commit()
    window = {"start": NOW.isoformat(), "end": (NOW + timedelta(days=1)).isoformat()}
    assert client.get("/api/v1/sessions", params=window).json()[0]["materials"][0]["title"] == "ER.pdf"
    assert client.get(f"/api/v1/tasks/{task.id}/materials").json()[0]["title"] == "ER.pdf"
    graph = client.get("/api/v1/knowledge/graph").json()
    assert graph["nodes"] and graph["links"]


def test_notes_endpoints_never_leave_the_staging_folder(client, tmp_path, monkeypatch):
    monkeypatch.setenv("PEACE_DATA_DIR", str(tmp_path))
    staging = tmp_path / "graphify" / "obsidian"
    (staging / ".obsidian").mkdir(parents=True)
    (staging / "Nota.md").write_text("# Nota")
    (staging / ".obsidian" / "app.json").write_text("{}")
    (tmp_path / "secret.md").write_text("x")
    assert [n["path"] for n in client.get("/api/v1/knowledge/notes").json()] == ["Nota.md"]
    assert client.get("/api/v1/knowledge/notes/file", params={"path": "Nota.md"}).text == "# Nota"
    for bad in ("../../secret.md", ".obsidian/app.json", "/etc/passwd"):
        assert client.get("/api/v1/knowledge/notes/file", params={"path": bad}).status_code == 404


# --- desktop: graph notes ------------------------------------------------------------------------

spec = importlib.util.spec_from_file_location("peace_desktop", Path(__file__).parents[1] / "desktop" / "peace_desktop.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class Resp:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_desktop_mirrors_notes_and_respects_user_edits(tmp_path, monkeypatch):
    vault = tmp_path / "Peace Vault"
    vault.mkdir()
    files = {"A.md": b"# A v1"}
    monkeypatch.setenv("PEACE_TOKEN", "test")
    monkeypatch.setattr(agent, "VAULT", vault)
    monkeypatch.setattr(agent, "STATE", tmp_path / "materials.json")
    monkeypatch.setattr(agent, "call", lambda *a: [{"path": p, "sha256": hashlib.sha256(b).hexdigest()} for p, b in files.items()])
    monkeypatch.setattr(agent.urllib.request, "urlopen",
                        lambda req, timeout: Resp(files[agent.urllib.parse.unquote(req.full_url.split("path=")[1])]))
    note = vault / "07 - Generated/Graphify/A.md"
    assert agent.mirror_notes() == 1 and note.read_bytes() == b"# A v1"
    note.write_bytes(b"# A with my edits")
    files["A.md"] = b"# A v2"
    assert agent.mirror_notes() == 0 and note.read_bytes() == b"# A with my edits"
    files.clear()
    agent.mirror_notes()
    assert note.exists()  # edited by the user: never deleted


# --- topics read by the local model, and purpose-aware suggestions -------------------------------

from peace import topics as topics_mod  # noqa: E402
from peace.knowledge import purpose, suggest_text  # noqa: E402


class _Answer:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": self.content}}


def test_topics_are_read_once_and_again_after_a_change(session, monkeypatch):
    m = Material(section="T", title="Normalização", kind="file", filename="n.pdf", source="moodle", external_id="n",
                 content="Formas normais 1FN 2FN 3FN", terms={"normal": 1.0}, fetched_at=NOW, modified_at=NOW)
    session.add(m)
    session.commit()
    calls = []

    def llm(method, path, **kw):
        calls.append(kw["json"])
        assert kw["json"]["format"]["properties"]["doc_kind"]["enum"] == list(topics_mod.KINDS)
        assert "Formas normais" in kw["json"]["messages"][0]["content"]  # file text goes in as data
        return _Answer('{"topics": ["formas normais", "3FN"], "doc_kind": "slides"}')

    monkeypatch.setattr(topics_mod, "_llm", llm)
    monkeypatch.setattr(topics_mod, "llm_reachable", lambda: True)
    assert topics_mod.analyze_pending(session, NOW) == 1
    session.refresh(m)
    assert (m.topics, m.doc_kind, m.topics_model) == (["formas normais", "3FN"], "slides", topics_mod.current_model())
    assert topics_mod.analyze_pending(session, NOW) == 0 and len(calls) == 1
    m.fetched_at = NOW + timedelta(days=1)  # a new version was downloaded
    session.commit()
    assert topics_mod.analyze_pending(session, NOW + timedelta(days=1)) == 1


def test_off_schema_answers_are_not_retried_and_offline_stops_the_batch(session, monkeypatch):
    for i in range(3):
        session.add(Material(section="T", title=f"F{i}", kind="file", filename=f"{i}.pdf", source="moodle", external_id=f"f{i}",
                             content="texto", terms={"texto": 1.0}, fetched_at=NOW, modified_at=NOW - timedelta(days=i)))
    session.commit()
    monkeypatch.setattr(topics_mod, "llm_reachable", lambda: True)
    monkeypatch.setattr(topics_mod, "_llm", lambda *a, **k: _Answer('{"topics": ["x"], "doc_kind": "poema"}'))
    assert topics_mod.analyze_pending(session, NOW, limit=1) == 0
    assert len(topics_mod.pending(session, 10)) == 2  # the odd one is marked, not retried forever
    state = {"up": True}

    def down(*a, **k):
        state["up"] = False
        raise httpx.ConnectError("PC off")

    monkeypatch.setattr(topics_mod, "_llm", down)
    monkeypatch.setattr(topics_mod, "llm_reachable", lambda: state["up"])
    assert topics_mod.analyze_pending(session, NOW) == 0
    assert len(topics_mod.pending(session, 10)) == 2  # untouched, retried when the PC is back


def test_purpose_from_the_task():
    assert purpose("BD (Teste 1)") == purpose("Frequência 1") == "exam"
    assert purpose("Meta3 - Modelos ER") == purpose("Submissao Lab 04") == "assignment"
    assert purpose("Rever capítulo 3", "assignment") == "assignment"
    assert purpose("Ler artigo sobre TCP") == "study"


def test_suggestions_weigh_topics_and_document_kind(session):
    bd = Course(name="Bases de Dados", code="1")
    session.add(bd)
    session.flush()

    def add(title, kind, topics, text, days=0):
        session.add(Material(course_id=bd.id, section="T", title=title, kind="file", filename=title, source="moodle", external_id=title,
                             terms=term_frequencies(text), topics=topics, doc_kind=kind, modified_at=NOW - timedelta(days=days)))

    add("Enunciado Meta3", "statement", ["modelo ER", "entidades"], "meta modelo entidade relacionamento entrega relatório modelo")
    add("Slides ER", "slides", ["modelo entidade relacionamento", "cardinalidade"], "modelo entidade relacionamento cardinalidade modelo")
    add("Avaliação", "admin", ["regras de avaliação"], "modelo entidade avaliação regras modelo entidade")
    add("Exame 2024", "past_exam", ["SQL", "normalização"], "exame normalização sql exame normalização sql", days=400)
    add("Ficha 3", "exercises", ["SQL joins"], "exercícios join sql exercícios join sql", days=10)
    session.commit()
    index = load_index(session)

    meta = suggest_text(index, "Meta3 - Modelo entidade relacionamento", bd.id, "assignment")
    assert meta[0]["title"] == "Enunciado Meta3" and meta[0]["why"].startswith("cobre modelo ER")
    assert "Avaliação" not in [s["title"] for s in meta]  # admin documents are never suggested
    vague = suggest_text(index, "Teste 2", bd.id, "exam")
    assert [s["title"] for s in vague] == ["Exame 2024", "Ficha 3", "Slides ER"]
    assert vague[0]["why"] == "exame de anos anteriores"
    assert suggest_text(index, "Ler artigo sobre astronomia", bd.id, "study") == []


def test_a_stray_description_word_is_not_a_topic_match(session):
    bd = Course(name="Bases de Dados", code="1")
    session.add(bd)
    session.flush()
    session.add(Material(course_id=bd.id, section="T", title="TP03", kind="file", filename="TP03", source="moodle", external_id="tp3",
                         terms=term_frequencies("funções grupo sql funções grupo sql"), topics=["Funções de grupo"], doc_kind="exercises"))
    session.commit()
    index = load_index(session)
    picks = suggest_text(index, "Meta3 Modelos ER Os grupos submetem o modelo", bd.id, "assignment", title="Meta3 Modelos ER")
    assert not any(p["why"].startswith("cobre") for p in picks)
    picks = suggest_text(index, "Ficha de funções de grupo", bd.id, "study", title="Ficha de funções de grupo")
    assert picks[0]["why"] == "cobre Funções de grupo"


def test_model_errors_are_not_blamed_on_the_file(session, monkeypatch):
    session.add(Material(section="T", title="F", kind="file", filename="f.pdf", source="moodle", external_id="f",
                         content="texto", terms={"texto": 1.0}, fetched_at=NOW, modified_at=NOW))
    session.commit()
    state = {"up": True}
    monkeypatch.setattr(topics_mod, "llm_reachable", lambda: state["up"])
    monkeypatch.setattr(topics_mod, "mark_unreachable", lambda: state.update(up=False))

    def server_error(*a, **k):
        request = httpx.Request("POST", "http://x/api/chat")
        raise httpx.HTTPStatusError("boom", request=request, response=httpx.Response(500, request=request))

    monkeypatch.setattr(topics_mod, "_llm", server_error)
    assert topics_mod.analyze_pending(session, NOW) == 0
    assert len(topics_mod.pending(session, 10)) == 1 and state["up"] is False  # retried later, file not marked
    state["up"] = True
    for body in ('["a list"]', '{"topics": null, "doc_kind": "slides"}'):  # malformed answers never crash the tick
        monkeypatch.setattr(topics_mod, "_llm", lambda *a, b=body, **k: _Answer(b))
        assert topics_mod.read_topics(topics_mod.pending(session, 1)[0]) is None


def test_exam_titles_keep_topical_acronyms(session):
    from peace.knowledge import course_acronym
    assert course_acronym("Métodos de Otimização e Análise de Dados") == "moad"
    bd = Course(name="Bases de Dados", code="1002")
    session.add(bd)
    session.flush()
    for title, kind, text in [("Exame 2024", "past_exam", "exame normalização exame normalização"),
                              ("Slides SQL", "slides", "sql select join sql select join")]:
        session.add(Material(course_id=bd.id, section="T", title=title, kind="file", filename=title, source="moodle",
                             external_id=title, terms=term_frequencies(text), doc_kind=kind))
    session.commit()
    index = load_index(session)
    assert suggest_text(index, "BD (Teste de SQL)", bd.id, "exam")[0]["title"] == "Slides SQL"  # "SQL" kept, "BD" dropped
    assert suggest_text(index, "BD (Teste 1)", bd.id, "exam")[0]["title"] == "Exame 2024"  # nothing topical: exam material

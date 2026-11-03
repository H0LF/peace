"""Reading files whole: every part, ZIP contents, copies and same-subject topics joined, and files that point at other files."""

import io
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from peace import archives, assistant, filelinks, improve, knowledge, library, llm, topics
from peace.models import Course, Material

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
LONG = " ".join(f"palavra{i} sobre bases de dados" for i in range(400))


@pytest.fixture
def course(session):
    c = Course(source="moodle", external_id="c1", name="Bases de Dados")
    session.add(c)
    session.commit()
    return c


@pytest.fixture(autouse=True)
def folder(monkeypatch, tmp_path):
    monkeypatch.setattr(library, "data_dir", lambda: tmp_path)
    (tmp_path / "materials").mkdir()
    return tmp_path


def add(session, course, name, content="", **kw):
    m = Material(source="moodle", external_id=name, course_id=course.id, kind="file", section="Aulas", title=name, filename=name, content=content, fetched_at=NOW, **kw)
    session.add(m)
    session.commit()
    return m


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)


# --- reading whole files ------------------------------------------------------------------------------------------


def test_every_part_of_a_long_file_is_read_then_joined(session, course, monkeypatch):
    m = add(session, course, "longo.pdf", "\n".join(f"Parte {i}. " + "x " * 3000 for i in range(5)))
    seen = []

    def ask(system, user, schema, timeout=180, purpose="local"):
        seen.append((user, purpose))
        if "topics" in schema["properties"] and "references" not in schema["properties"]:
            return {"topics": ["Junto"], "summary": "Tudo.", "doc_kind": "slides"}
        n = len(seen)
        return {"topics": [f"Tema {n}", "Comum"], "doc_kind": "slides", "summary": f"Parte {n}.", "references": []}

    monkeypatch.setattr(topics.llm, "ask_json", ask)
    out = topics.read_full(m)
    reads = [u for u, _ in seen if u.startswith("Parte ")]
    assert len(reads) >= 3 and all(p == "documents" for _, p in seen)
    assert "Tema 1" in seen[-1][0] and "Comum" in seen[-1][0]  # the joining step sees all parts
    assert out["topics"] == ["Junto"] and out["summary"] == "Tudo."


def test_a_failed_part_leaves_the_file_for_later_and_data_files_send_only_their_start(session, course, monkeypatch):
    m = add(session, course, "longo.pdf", "\n".join("x " * 3000 for _ in range(4)))
    monkeypatch.setattr(topics.llm, "ask_json", lambda *a, **k: None)
    assert topics.read_full(m) is None
    calls = []
    monkeypatch.setattr(topics.llm, "ask_json", lambda *a, **k: calls.append(1) or {"topics": ["Bolsa"], "doc_kind": "other", "summary": "s", "references": []})
    data = add(session, course, "precos.csv", "\n".join("1,2,3 " * 2000 for _ in range(4)))
    assert topics.read_full(data)["topics"] == ["Bolsa"] and len(calls) == 1


def test_a_file_name_the_text_does_not_contain_is_thrown_away(session, course, monkeypatch):
    m = add(session, course, "guia.pdf", "Usa o ficheiro dados.zip na primeira aula. Submete grupo_3.zip no fim.")
    monkeypatch.setattr(topics.llm, "ask_json", lambda *a, **k: {"topics": ["SQL"], "doc_kind": "statement", "summary": "s", "references": [
        {"name": "dados.zip", "why": "tem os dados", "action": "abrir"}, {"name": "grupo_3.zip", "why": "a entrega", "action": "entregar"}, {"name": "inventado.zip", "why": "x", "action": "abrir"}]})
    assert [r["name"] for r in topics.read_full(m)["references"]] == ["dados.zip", "grupo_3.zip"]


# --- archives ------------------------------------------------------------------------------------------------------


def test_a_zip_becomes_files_that_can_be_read_and_opened(session, course, folder):
    z = add(session, course, "dados.zip")
    make_zip(library.cached_file(z.id), {"pasta/guia.txt": "Instruções da aula.", "__MACOSX/lixo.txt": "x", "pasta/virus.exe": "MZ"})
    assert archives.expand_pending(session, NOW) == 1
    session.expire_all()
    (member,) = session.query(Material).filter_by(parent_id=z.id).all()
    assert member.filename == "guia.txt" and member.content == "Instruções da aula." and member.course_id == course.id and member.source == "archive"
    assert library.cached_file(member.id).read_text() == "Instruções da aula."
    assert "pasta/guia.txt" in z.content
    assert archives.expand_pending(session, NOW) == 0  # not opened twice


def test_a_new_version_of_a_zip_drops_what_it_no_longer_has(session, course):
    z = add(session, course, "dados.zip")
    make_zip(library.cached_file(z.id), {"a.txt": "um", "b.txt": "dois"})
    archives.expand(session, z, NOW)
    session.commit()
    make_zip(library.cached_file(z.id), {"a.txt": "um mudado"})
    archives.expand(session, z, NOW)
    session.commit()
    session.expire_all()
    (only,) = session.query(Material).filter_by(parent_id=z.id).all()
    assert only.filename == "a.txt" and only.content == "um mudado"


@pytest.mark.parametrize("hostile", ["../../../etc/passwd", "/abs/olute.txt", "..\\..\\win.txt"])
def test_a_hostile_path_stays_inside_peaces_own_folder(session, course, folder, hostile):
    z = add(session, course, "mau.zip")
    make_zip(library.cached_file(z.id), {hostile: "x"})
    archives.expand(session, z, NOW)
    session.commit()
    (m,) = session.query(Material).filter_by(parent_id=z.id).all()
    assert library.cached_file(m.id).parent == folder / "materials" and "/" not in m.filename and "\\" not in m.filename


def test_oversized_members_and_broken_archives_are_refused_safely(session, course, monkeypatch):
    monkeypatch.setattr(archives, "MAX_MEMBER", 100)
    z = add(session, course, "grande.zip")
    make_zip(library.cached_file(z.id), {"grande.txt": "x" * 5000, "pequeno.txt": "ok"})
    archives.expand(session, z, NOW)
    assert [m.filename for m in session.query(Material).filter_by(parent_id=z.id)] == ["pequeno.txt"]
    broken = add(session, course, "partido.zip")
    library.cached_file(broken.id).write_bytes(b"isto nao e um zip")
    assert archives.expand(session, broken, NOW) == 0 and broken.content == archives.UNREADABLE


def test_a_zip_inside_a_zip_is_opened_once_and_no_deeper(session, course):
    inner2 = io.BytesIO()
    with zipfile.ZipFile(inner2, "w") as z:
        z.writestr("fundo.txt", "fundo")
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("meio.txt", "meio")
        z.writestr("nivel2.zip", inner2.getvalue())
    top = add(session, course, "topo.zip")
    make_zip(library.cached_file(top.id), {"nivel1.zip": inner.getvalue()})
    archives.expand(session, top, NOW)
    session.commit()
    names = {m.filename for m in session.query(Material).filter(Material.source == "archive")}
    assert names == {"nivel1.zip", "meio.txt", "nivel2.zip"}


# --- copies ---------------------------------------------------------------------------------------------------------


def test_copies_count_once_the_newest_stands_and_other_courses_keep_theirs(session, course):
    other = Course(source="moodle", external_id="c2", name="Outra")
    session.add(other)
    session.commit()
    old = add(session, course, "aula.pdf", LONG, modified_at=NOW - timedelta(days=9))
    new = add(session, course, "aula (1).pdf", "[p. 1] " + LONG, modified_at=NOW)
    different = add(session, course, "outra.pdf", " ".join(f"tema{i} completamente diferente aqui" for i in range(400)))
    elsewhere = Material(source="moodle", external_id="noutra", course_id=other.id, kind="file", section="s", title="aula.pdf", filename="aula.pdf", content=LONG, fetched_at=NOW)
    session.add(elsewhere)
    session.commit()
    assert filelinks.mark_duplicates(session) == 1
    assert old.duplicate_of_id == new.id and new.duplicate_of_id is None
    assert different.duplicate_of_id is None and elsewhere.duplicate_of_id is None


def test_a_copy_stops_being_one_when_it_changes(session, course):
    a = add(session, course, "a.pdf", LONG, modified_at=NOW - timedelta(days=1))
    b = add(session, course, "b.pdf", LONG, modified_at=NOW)
    filelinks.mark_duplicates(session)
    assert a.duplicate_of_id == b.id
    a.content = " ".join(f"novo{i} texto totalmente outro mesmo" for i in range(400))
    session.commit()
    filelinks.mark_duplicates(session)
    assert a.duplicate_of_id is None


def test_copies_leave_search_and_the_graph_but_stay_listed(session, course, client):
    old = add(session, course, "aula.pdf", LONG, modified_at=NOW - timedelta(days=9), terms={"dados": 1.0})
    new = add(session, course, "aula2.pdf", LONG, modified_at=NOW, terms={"dados": 1.0})
    filelinks.mark_duplicates(session)
    assert f"material:{old.id}" not in knowledge.build_graph(session, NOW) and f"material:{new.id}" in knowledge.build_graph(session, NOW)
    from peace import answers
    assert {e.material.id for e in answers.search(session, "palavra1 dados")} == {new.id}
    row = next(r for r in client.get("/api/v1/materials").json() if r["id"] == old.id)
    assert row["duplicate_of_id"] == new.id


def test_one_document_in_two_languages_counts_once_and_stays_so(session, course, monkeypatch):
    pt = add(session, course, "Enunciado.pdf", "texto em português " * 50, topics=["SQL", "Linguagem SQL"], doc_kind="statement")
    en = add(session, course, "Statement (ERASMUS).pdf", "text in english here " * 50, topics=["SQL language"], doc_kind="statement")
    monkeypatch.setattr(improve.llm, "ask_json", lambda *a, **k: {"groups": [{"name": "Linguagem SQL", "topics": ["SQL", "Linguagem SQL", "SQL language", "inventado"]}], "same_documents": [[en.id, pt.id]]})
    joined, answered = improve._join(session, course)
    assert answered and joined == 3 and pt.topics == ["Linguagem SQL"] and en.topics == ["Linguagem SQL"]
    assert en.duplicate_of_id == pt.id and pt.duplicate_of_id is None
    session.commit()
    filelinks.mark_duplicates(session)
    assert en.duplicate_of_id == pt.id  # text alone would undo it


# --- references -----------------------------------------------------------------------------------------------------


def test_opening_a_file_announces_what_it_needs_and_a_hand_in_is_not_a_file_to_open(session, course, monkeypatch):
    target = add(session, course, "dados.zip", "Este arquivo contém: a.txt")
    guide = add(session, course, "guia.pdf", "Usa dados.zip. Submete grupo.zip.")
    add(session, course, "grupo.zip", "Este arquivo contém: b.txt")
    guide.file_refs = filelinks.resolve(session, guide, [
        {"name": "dados.zip", "why": "tem os dados da aula", "action": "abrir"}, {"name": "grupo.zip", "why": "a entrega", "action": "entregar"}])
    session.commit()
    by_name = {r["name"]: r for r in guide.file_refs}
    assert by_name["dados.zip"]["material_id"] == target.id and by_name["grupo.zip"]["material_id"] is None
    monkeypatch.setattr(assistant.reading, "find", lambda s, q, f: guide)
    reply = assistant.cmd_document(session, NOW, "open", "guia")
    assert "usares também: dados.zip" in reply.text and "grupo.zip" not in reply.text


def test_a_file_naming_a_real_file_is_linked_without_any_model_and_shows_in_the_graph(session, course):
    target = add(session, course, "Ficha3.pdf", "exercícios", terms={"ficha": 1.0})
    guide = add(session, course, "guia.pdf", "Resolve primeiro a Ficha3.pdf e depois inexistente.pdf.", terms={"guia": 1.0})
    filelinks.refresh_literal(session)
    assert [(r["name"], r["material_id"]) for r in guide.file_refs] == [("Ficha3.pdf", target.id)]
    edge = knowledge.build_graph(session, NOW).edges[f"material:{guide.id}", f"material:{target.id}"]
    assert edge["relation"] == "needs" and edge["confidence"] == "EXTRACTED"


def test_a_zip_is_not_told_to_open_what_it_holds(session, course):
    z = add(session, course, "dados.zip")
    make_zip(library.cached_file(z.id), {"Ficha.pdf": "x"})
    archives.expand(session, z, NOW)
    session.commit()
    filelinks.refresh_literal(session)
    assert z.file_refs == []


def test_a_cloud_model_takes_bigger_parts_and_a_data_dump_only_shows_its_ends(monkeypatch):
    long_text = "\n".join("frase de matéria " * 400 for _ in range(30))
    monkeypatch.setattr(topics.llm, "route", lambda purpose="local": None)
    local = topics.parts_of("aula.pdf", long_text)
    monkeypatch.setattr(topics.llm, "route", lambda purpose="local": ("chatgpt_plan", "x"))
    cloud = topics.parts_of("aula.pdf", long_text)
    assert len(cloud) < len(local) and "".join(cloud) == long_text == "".join(local)  # a document is read whole either way
    dump = "\n".join("INSERT INTO t VALUES (1); " * 100 for _ in range(200))
    assert len(topics.parts_of("carga.sql", dump)) == 2 and len(topics.parts_of("dados.csv", dump)) == 1
    big_pdf = "\n".join("texto " * 1000 for _ in range(100))
    assert "".join(topics.parts_of("livro.pdf", big_pdf)) == big_pdf


def test_later_versions_are_not_translations_and_portuguese_stands_for_both(session, course, monkeypatch):
    old = add(session, course, "Trabs_09-23.pdf", "texto " * 200, topics=["A"], doc_kind="statement")
    new = add(session, course, "Trabs_09-29.pdf", "texto " * 400, topics=["B"], doc_kind="statement")
    en = add(session, course, "udp-en.docx", "english words here " * 40, topics=["C"], doc_kind="exercises")
    pt = add(session, course, "udp.pdf", "palavras em portugues " * 35, topics=["D"], doc_kind="exercises")
    monkeypatch.setattr(improve.llm, "ask_json", lambda *a, **k: {"groups": [], "same_documents": [[old.id, new.id], [en.id, pt.id]]})
    improve._join(session, course)
    assert old.duplicate_of_id is None and new.duplicate_of_id is None
    assert en.duplicate_of_id == pt.id and pt.duplicate_of_id is None


def test_a_wrong_translation_mark_from_before_is_undone(session, course):
    primary = add(session, course, "a.pdf", "x " * 200)
    wrong = add(session, course, "b.pdf", "y " * 500)
    filelinks.mark_translation(wrong, primary)
    session.commit()
    filelinks.mark_duplicates(session)
    assert wrong.duplicate_of_id is None


def test_a_zip_full_of_small_texts_is_a_data_set_not_course_knowledge(session, course):
    z = add(session, course, "reviews.zip")
    make_zip(library.cached_file(z.id), {**{f"cv{i:03}.txt": f"film review number {i}" for i in range(25)}, "guia.pdf": "x", "notas.txt": "nota"})
    archives.expand(session, z, NOW)
    session.commit()
    assert filelinks.mark_datasets(session) == 23  # 26 texts, 3 samples
    filelinks.mark_duplicates(session)
    kept = session.query(Material).filter(Material.parent_id == z.id, Material.filename.like("%.txt"), Material.duplicate_of_id.is_(None)).count()
    assert kept == 3


def test_a_web_address_or_a_place_is_not_a_missing_file(session, course):
    m = add(session, course, "guia.pdf", "x")
    refs = filelinks.resolve(session, m, [{"name": "https://scikit-learn.org/doc", "why": "", "action": "abrir"}, {"name": "moodle (Aula 04)", "why": "", "action": "entregar"}, {"name": "optdigits.tra", "why": "treino", "action": "abrir"}])
    assert [r["name"] for r in refs] == ["optdigits.tra"]

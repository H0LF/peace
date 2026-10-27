import importlib.util
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pytest

from peace import sync
from peace.models import Change, Material
from peace.sources import SourceError
from peace.sources.moodle import PLUGINFILE, Snapshot, parse_materials
from peace.sync import run_sync, vault_path

NOW = datetime(2026, 10, 26, 13, 0, tzinfo=UTC)
COURSE = {"id": 25944, "code": "60000003", "name": "Introdução às Redes de Comunicação", "url": None}


def contents(extra_file=None):
    files = [{"type": "file", "filename": "1-intro.pdf", "filepath": "/", "filesize": 1000, "mimetype": "application/pdf",
              "timemodified": 1790000000, "fileurl": PLUGINFILE + "1/mod_resource/content/1/1-intro.pdf"}]
    folder = [
        {"type": "file", "filename": "ficha1.pdf", "filepath": "/fichas/", "filesize": 10, "mimetype": "application/pdf",
         "timemodified": 1790000000, "fileurl": PLUGINFILE + "2/mod_folder/content/1/fichas/ficha1.pdf"},
        {"type": "file", "filename": "evil.pdf", "filepath": "/", "filesize": 1, "timemodified": 1, "fileurl": "https://evil.example/x"},
    ]
    if extra_file:
        folder.append(extra_file)
    return [
        {"name": "Teóricas", "modules": [
            {"id": 1, "modname": "resource", "name": "Slides: Introdução", "url": "https://moodle.example.pt/moodle/mod/resource/view.php?id=1", "contents": files},
            {"id": 3, "modname": "url", "name": "RFC 793", "contents": [{"type": "url", "fileurl": "https://www.rfc-editor.org/rfc/rfc793"}]},
            {"id": 4, "modname": "url", "name": "Mau", "contents": [{"type": "url", "fileurl": "javascript:alert(1)"}]},
            {"id": 5, "modname": "quiz", "name": "Quiz", "contents": []},
            {"id": 6, "modname": "resource", "name": "Escondido", "uservisible": False, "contents": files},
        ]},
        {"name": "", "modules": [{"id": 2, "modname": "folder", "name": "Fichas práticas", "contents": folder}]},
    ]


def test_parse_files_folders_and_links_only_from_moodle():
    items = parse_materials(25944, contents())
    assert [(m["kind"], m["title"], m["section"]) for m in items] == [
        ("file", "Slides: Introdução", "Teóricas"),
        ("link", "RFC 793", "Teóricas"),
        ("file", "ficha1.pdf", "Geral"),
    ]
    assert items[2]["external_id"] == "file:2:/fichas/ficha1.pdf" and items[2]["folder"] == "Fichas práticas"


def test_vault_paths_are_safe_and_unique():
    used: set[str] = set()
    m = {"section": "../Teóricas: semana 1", "folder": ".hidden", "subpath": "a/../b", "filename": "slides?.pdf"}
    first = vault_path("Redes / Comunicação", m, used)
    assert first == "02 - University/Courses/Redes - Comunicação/Materiais/-Teóricas- semana 1/hidden/a/b/slides-.pdf"
    assert ".." not in first.split("/")
    assert vault_path("Redes / Comunicação", m, used).endswith("slides- (2).pdf")
    assert vault_path(None, {"section": "S", "folder": None, "subpath": "", "filename": "README"}, used).endswith("Sem cadeira/Materiais/S/README")


def sync_materials(items, courses=(COURSE,)):
    return run_sync("moodle", "manual", now=NOW, fetch=lambda _n: Snapshot(list(courses), [], items))


def test_first_import_is_silent_then_new_and_removed_materials(session):
    assert sync_materials(parse_materials(25944, contents())).status == "success"
    assert session.query(Material).count() == 3
    assert session.query(Change).filter(Change.entity == "material").count() == 0
    rows = {m.title: m for m in session.query(Material)}
    assert rows["Slides: Introdução"].vault_path.endswith("Materiais/Teóricas/1-intro.pdf")
    assert rows["RFC 793"].vault_path is None

    new = {"type": "file", "filename": "ficha2.pdf", "filepath": "/", "filesize": 5, "timemodified": 1790000500,
           "fileurl": PLUGINFILE + "2/mod_folder/content/1/ficha2.pdf"}
    sync_materials([m for m in parse_materials(25944, contents(new)) if m["kind"] != "link"])
    created = session.query(Change).filter(Change.entity == "material", Change.kind == "created")
    assert [c.summary for c in created] == ["Novo material: ficha2.pdf"]
    assert "RFC 793" not in {m.title for m in session.query(Material)}  # gone upstream


def test_empty_contents_never_forget_known_materials(session):
    sync_materials(parse_materials(25944, contents()))
    run = sync_materials([])
    assert run.status == "failed" and session.query(Material).count() == 3


def test_download_refuses_non_moodle_urls():
    with pytest.raises(SourceError):
        sync.open_moodle_file("https://evil.example/file.pdf")


def test_materials_api_and_file_endpoint_for_links(client):
    sync_materials(parse_materials(25944, contents()))
    files = client.get("/api/v1/materials", params={"kind": "file"}).json()
    assert [f["filename"] for f in files] == ["ficha1.pdf", "1-intro.pdf"]  # section "Geral" sorts first
    link = next(m for m in client.get("/api/v1/materials").json() if m["kind"] == "link")
    assert client.get(f"/api/v1/materials/{link['id']}/file").status_code == 404


# --- desktop mirror ------------------------------------------------------------------------------

spec = importlib.util.spec_from_file_location("peace_desktop", Path(__file__).parents[1] / "desktop" / "peace_desktop.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    vault = tmp_path / "Peace Vault"
    vault.mkdir()
    remote = {"items": [], "files": {}}
    monkeypatch.setattr(agent, "VAULT", vault)
    monkeypatch.setattr(agent, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(agent, "call", lambda method, path, body=None: remote["items"])

    def fake_download(mid, target):
        if mid not in remote["files"]:
            raise urllib.error.HTTPError(f"/materials/{mid}/file", 404, "ficheiro ainda não descarregado", None, None)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(remote["files"][mid])

    monkeypatch.setattr(agent, "download", fake_download)
    return vault, remote


def item(mid=1, path="02 - University/Courses/RC/Materiais/T/a.pdf", modified="2026-10-01T00:00:00Z", size=3):
    return {"id": mid, "vault_path": path, "modified_at": modified, "size": size}


def test_mirror_downloads_once_and_updates_untouched_copies(mirror):
    vault, remote = mirror
    remote["items"], remote["files"] = [item()], {1: b"v1"}
    assert agent.mirror_materials() == 1
    assert agent.mirror_materials() == 0  # unchanged: nothing written
    target = vault / "02 - University/Courses/RC/Materiais/T/a.pdf"
    remote["items"], remote["files"] = [item(modified="2026-10-05T00:00:00Z")], {1: b"v2"}
    assert agent.mirror_materials() == 1 and target.read_bytes() == b"v2"
    remote["items"], remote["files"] = [item(mid=99, modified="2026-10-05T00:00:00Z")], {99: b"v2"}
    assert agent.mirror_materials() == 0  # same file under a new server id: not copied again


def test_mirror_never_overwrites_user_files(mirror):
    vault, remote = mirror
    mine = vault / "02 - University/Courses/RC/Materiais/T/a.pdf"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"my notes")
    remote["items"], remote["files"] = [item()], {1: b"teacher"}
    agent.mirror_materials()
    assert mine.read_bytes() == b"my notes"
    assert (mine.parent / "a (PEACE 2026-10-01).pdf").read_bytes() == b"teacher"


def test_mirror_refuses_paths_outside_the_vault_or_hidden(mirror):
    vault, remote = mirror
    remote["items"] = [item(1, "../outside.pdf"), item(2, ".obsidian/app.json"), item(3, "/etc/passwd"), item(4, None)]
    remote["files"] = {1: b"x", 2: b"x", 3: b"x", 4: b"x"}
    assert agent.mirror_materials() == 0
    assert not (vault.parent / "outside.pdf").exists() and not (vault / ".obsidian").exists()


def test_mirror_skips_files_over_the_size_cap(mirror, monkeypatch):
    vault, remote = mirror
    monkeypatch.setattr(agent, "MAX_BYTES", 2)
    remote["items"], remote["files"] = [item(size=3)], {1: b"big"}
    assert agent.mirror_materials() == 0
    assert not any(vault.rglob("*.pdf"))


def test_a_file_the_server_has_not_fetched_yet_does_not_block_the_others(mirror):
    vault, remote = mirror
    remote["items"] = [item(1, "RC/a.pdf"), item(2, "RC/b.pdf")]
    remote["files"] = {2: b"ready"}
    assert agent.mirror_materials() == 1 and (vault / "RC/b.pdf").read_bytes() == b"ready"
    remote["files"][1] = b"now ready"
    assert agent.mirror_materials() == 1 and (vault / "RC/a.pdf").read_bytes() == b"now ready"

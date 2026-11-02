"""Obsidian link: the desktop agent reports the vaults Obsidian knows, Settings picks one, the agent follows it."""

import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("peace_desktop", Path(__file__).parents[1] / "desktop" / "peace_desktop.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

PEACE = {"name": "Peace Vault", "path": "/home/ana/Peace Vault"}
OTHER = {"name": "Work", "path": "/home/ana/Work"}


def beat(client, vaults, current=None):
    caps = {"vaults": vaults, "vault": current}
    assert client.post("/api/v1/desktop/heartbeat", json={"capabilities": caps}).status_code == 204


def test_single_vault_is_used_without_choosing(client):
    beat(client, [OTHER])
    assert client.get("/api/v1/preferences/obsidian").json() == {"chosen": "", "vaults": [OTHER], "vault": OTHER, "pinned": False}


def test_several_vaults_default_to_peace_vault_then_follow_the_choice(client):
    beat(client, [PEACE, OTHER])
    assert client.get("/api/v1/preferences/obsidian").json()["vault"] == PEACE
    assert client.put("/api/v1/preferences/obsidian", json={"path": OTHER["path"]}).json()["vault"] == OTHER
    assert client.get("/api/v1/preferences/obsidian").json()["chosen"] == OTHER["path"]


def test_several_vaults_without_peace_vault_wait_for_a_choice(client):
    beat(client, [OTHER, {"name": "Notes", "path": "/home/ana/Notes"}])
    assert client.get("/api/v1/preferences/obsidian").json()["vault"] is None


def test_unknown_vault_is_refused(client):
    beat(client, [PEACE])
    assert client.put("/api/v1/preferences/obsidian", json={"path": "/home/ana/.ssh"}).status_code == 422


def test_chosen_vault_that_disappeared_is_not_replaced_by_another(client):
    beat(client, [PEACE, OTHER])
    client.put("/api/v1/preferences/obsidian", json={"path": OTHER["path"]})
    beat(client, [PEACE])
    assert client.get("/api/v1/preferences/obsidian").json()["vault"] is None


def test_no_desktop_yet(client):
    assert client.get("/api/v1/preferences/obsidian").json() == {"chosen": "", "vaults": [], "vault": None, "pinned": False}


def test_automatic_keeps_the_folder_the_agent_already_uses(client):
    """An existing ~/Peace Vault that Obsidian does not list must not be swapped for the one vault it does list."""
    beat(client, [OTHER], {"path": "/home/ana/Peace Vault"})
    assert client.get("/api/v1/preferences/obsidian").json()["vault"] == {"name": "Peace Vault", "path": "/home/ana/Peace Vault"}
    client.put("/api/v1/preferences/obsidian", json={"path": OTHER["path"]})
    beat(client, [OTHER], {"path": "/home/ana/Peace Vault"})
    assert client.get("/api/v1/preferences/obsidian").json()["vault"] == OTHER  # an explicit choice still wins


def test_pinned_vault_is_shown_whatever_was_chosen(client):
    beat(client, [PEACE, OTHER])
    client.put("/api/v1/preferences/obsidian", json={"path": OTHER["path"]})
    beat(client, [PEACE, OTHER], {"path": PEACE["path"], "pinned": True})
    body = client.get("/api/v1/preferences/obsidian").json()
    assert body["vault"] == PEACE and body["pinned"] is True


# --- agent ---------------------------------------------------------------------------------------------------

def _config(path: Path, vaults: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"vaults": vaults}))
    return path


def test_agent_finds_native_and_flatpak_vaults_and_skips_bad_entries(tmp_path, monkeypatch):
    a, b = tmp_path / "Peace Vault", tmp_path / "Work"
    a.mkdir(), b.mkdir()
    native = _config(tmp_path / "native.json", {"1": {"path": str(a)}, "2": {"path": str(tmp_path / "gone")}, "3": "junk"})
    flatpak = _config(tmp_path / "flatpak.json", {"x": {"path": str(b)}, "y": {"path": str(a)}})
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    monkeypatch.setattr(agent.actions, "OBSIDIAN_CONFIGS", [native, flatpak, broken, tmp_path / "missing.json"])
    assert agent.actions.obsidian_vaults() == [{"name": "Peace Vault", "path": str(a.resolve())},
                                               {"name": "Work", "path": str(b.resolve())}]


def test_agent_follows_only_a_vault_it_detected(tmp_path, monkeypatch):
    vault = tmp_path / "Work"
    vault.mkdir()
    monkeypatch.setattr(agent, "VAULT_PINNED", False)
    monkeypatch.setattr(agent, "VAULT", tmp_path / "old")
    monkeypatch.setattr(agent.actions, "VAULT", tmp_path / "old")
    monkeypatch.setattr(agent.actions, "obsidian_vaults", lambda: [{"name": "Work", "path": str(vault)}])
    agent.apply_vault({"chosen": str(tmp_path), "vault": {"name": "Evil", "path": str(tmp_path)}})
    assert agent.VAULT == tmp_path / "old"
    agent.apply_vault({"vault": {"name": "Work", "path": str(vault)}})  # automatic, no folder yet: found one
    assert agent.VAULT == agent.actions.VAULT == vault and agent.vault_changed.is_set()


def test_automatic_never_moves_an_existing_folder(tmp_path, monkeypatch):
    old, work = tmp_path / "Peace Vault", tmp_path / "Work"
    old.mkdir(), work.mkdir()
    monkeypatch.setattr(agent, "VAULT_PINNED", False)
    monkeypatch.setattr(agent, "VAULT", old)
    monkeypatch.setattr(agent.actions, "obsidian_vaults", lambda: [{"name": "Work", "path": str(work)}])
    agent.apply_vault({"chosen": "", "vault": {"name": "Work", "path": str(work)}})
    assert agent.VAULT == old
    agent.apply_vault({"chosen": str(work), "vault": {"name": "Work", "path": str(work)}})
    assert agent.VAULT == work


def test_switching_vault_copies_everything_into_the_new_one(tmp_path, monkeypatch):
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir(), b.mkdir()
    monkeypatch.setattr(agent, "STATE", tmp_path / "state" / "materials.json")
    monkeypatch.setenv("PEACE_TOKEN", "t")
    notes = [{"path": "n.md", "sha256": __import__("hashlib").sha256(b"note").hexdigest()}]
    monkeypatch.setattr(agent, "call", lambda method, path, body=None: notes if path == "/api/v1/knowledge/notes" else [])

    class Body:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"note"
    monkeypatch.setattr(agent.urllib.request, "urlopen", lambda *a, **k: Body())
    monkeypatch.setattr(agent, "VAULT", a)
    assert agent._mirror("/api/v1/knowledge/notes", "G", "notes.json") == 1
    assert agent._mirror("/api/v1/knowledge/notes", "G", "notes.json") == 0
    monkeypatch.setattr(agent, "VAULT", b)
    assert agent._mirror("/api/v1/knowledge/notes", "G", "notes.json") == 1 and (b / "G/n.md").read_bytes() == b"note"
    assert (a / "G/n.md").exists()  # the old vault keeps its copy


def test_peace_vault_env_pins_the_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "VAULT_PINNED", True)
    monkeypatch.setattr(agent, "VAULT", tmp_path / "pinned")
    monkeypatch.setattr(agent.actions, "obsidian_vaults", lambda: [{"name": "W", "path": str(tmp_path)}])
    agent.apply_vault({"vault": {"name": "W", "path": str(tmp_path)}})
    assert agent.VAULT == tmp_path / "pinned"

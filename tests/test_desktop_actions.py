"""The desktop action controller (desktop/peace_actions.py), against a fake Hyprland socket and a fake tab
bridge in a throwaway runtime directory. Nothing here touches the real desktop, files outside tmp, or Brave."""

import importlib.util
import json
import re
import shutil
import socketserver
import tempfile
import threading
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("peace_actions", Path(__file__).parents[1] / "desktop" / "peace_actions.py")
actions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(actions)

DSP = re.compile(r"^hl\.dsp\.([a-z_.]+)\(\{ (.*) \}\)$")
ARG = re.compile(r'(\w+) = ("[^"]*"|-?\d+|true|false)')


class FakeHyprland:
    """Answers the request socket like Hyprland 0.56 and applies the dispatchers PEACE sends."""

    def __init__(self, root: Path, version: str = "v0.56.2"):
        self.version = version
        self.clients = []
        self.active_ws = 1
        self.sent: list[str] = []
        self.refuse = False
        self.ignore_close = False
        self.monitors = [{"id": 0, "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1, "reserved": [0, 40, 0, 0]}]
        path = root / "hypr" / "sig" / ".socket.sock"
        path.parent.mkdir(parents=True)
        fake = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                command = self.request.recv(65536).decode()
                self.request.sendall(fake.answer(command).encode())

        self.server = socketserver.ThreadingUnixStreamServer(str(path), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()

    def add(self, address, cls, ws=1, title="", floating=False, focus=9):
        self.clients.append({"address": address, "class": cls, "initialClass": cls, "title": title, "mapped": True, "hidden": False,
                             "workspace": {"id": ws, "name": str(ws)}, "floating": floating, "at": [0, 40], "size": [960, 1040],
                             "monitor": 0, "focusHistoryID": focus})

    def get(self, address):
        return next(c for c in self.clients if c["address"] == address)

    def answer(self, command: str) -> str:
        if command.startswith("j/"):
            active = min(self.clients, key=lambda c: c.get("focusHistoryID", 99), default={})
            counts: dict[int, int] = {}
            for c in self.clients:
                counts[c["workspace"]["id"]] = counts.get(c["workspace"]["id"], 0) + 1
            return json.dumps({"version": {"tag": self.version}, "clients": self.clients, "monitors": self.monitors,
                               "activewindow": active, "activeworkspace": {"id": self.active_ws},
                               "workspaces": [{"id": i, "name": str(i), "windows": n} for i, n in counts.items()]}[command[2:]])
        self.sent.append(command)
        m = DSP.match(command.removeprefix("dispatch "))
        if self.refuse or not m:
            return "error: refused"
        name, args = m[1], {k: json.loads(v) for k, v in ARG.findall(m[2])}
        target = next((c for c in self.clients if "window" in args and c["address"] == args["window"].removeprefix("address:")), None)
        if name == "focus" and "workspace" in args:
            self.active_ws = args["workspace"]
        elif name == "focus" and target:
            for c in self.clients:
                c["focusHistoryID"] += 1
            target["focusHistoryID"] = 0
        elif name == "window.move" and target and "workspace" in args:
            target["workspace"] = {"id": args["workspace"], "name": str(args["workspace"])}
        elif name == "window.move" and target:
            target["at"] = [args["x"], args["y"]]
        elif name == "window.resize" and target:
            target["size"] = [args["x"], args["y"]]
        elif name == "window.float" and target:
            target["floating"] = args["action"] == "set"
        elif name == "window.close" and target and not self.ignore_close:
            self.clients.remove(target)
        return "ok"


class FakeBridge:
    """The native host's socket, answering like the extension (ids, groups, closed tabs)."""

    def __init__(self, path: Path):
        self.tabs = [{"id": 1, "windowId": 10, "index": 0, "active": True, "groupId": -1, "title": "Moodle - Sistemas Operativos",
                      "url": "https://moodle.example/course/view.php"},
                     {"id": 2, "windowId": 10, "index": 1, "active": False, "groupId": -1, "title": "YouTube", "url": "https://www.youtube.com/watch"},
                     {"id": 3, "windowId": 10, "index": 2, "active": False, "groupId": -1, "title": "SO - Escalonamento", "url": "https://wiki.example/so"}]
        self.requests = []
        self.wrong_id = False
        fake = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                request = json.loads(self.rfile.readline())
                fake.requests.append(request)
                reply = {"id": "other" if fake.wrong_id else request["id"], "ok": True, "result": fake.perform(request["op"], request["args"])}
                self.wfile.write(json.dumps(reply).encode() + b"\n")

        self.server = socketserver.ThreadingUnixStreamServer(str(path), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()

    def perform(self, op, args):
        if op == "list":
            return self.tabs
        if op == "open":
            tab = {"id": 9, "windowId": 10, "index": 3, "active": True, "groupId": -1, "title": "", "url": args["url"]}
            self.tabs.append(tab)
            return tab
        if op == "group":
            for t in self.tabs:
                if t["id"] in args["tabIds"]:
                    t["groupId"] = 77
            return 77
        if op == "close":
            self.tabs = [t for t in self.tabs if t["id"] not in args["tabIds"]]
        if op == "focus":
            for t in self.tabs:
                t["active"] = t["id"] == args["tabId"]
        return None


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    """A short private runtime dir (AF_UNIX paths are limited to 108 bytes); state and config in tmp."""
    root = Path(tempfile.mkdtemp(prefix="pa"))
    monkeypatch.setattr(actions, "RUNTIME", root)
    monkeypatch.setattr(actions, "BROWSER_SOCKET", root / "peace-browser.sock")
    monkeypatch.setattr(actions, "AUDIT_FILE", tmp_path / "state" / "actions.jsonl")
    monkeypatch.setattr(actions, "UNDO_FILE", tmp_path / "state" / "layout-undo.json")
    monkeypatch.setattr(actions, "LAYOUTS_DIR", tmp_path / "config" / "layouts")
    monkeypatch.setattr(actions, "ENABLED", set(actions.CATEGORIES))
    monkeypatch.setattr(actions, "_hypr", {})
    monkeypatch.setattr(actions, "LAUNCH_WAIT", 0.3)
    monkeypatch.setattr(actions, "OPEN_WAIT", 0.3)
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "sig")
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def hypr(runtime):
    fake = FakeHyprland(runtime)
    yield fake
    fake.server.shutdown()


@pytest.fixture
def roots(monkeypatch, tmp_path):
    vault, docs, outside = tmp_path / "vault", tmp_path / "docs", tmp_path / "outside"
    for d in (vault / "SO", docs, outside, vault / ".obsidian"):
        d.mkdir(parents=True)
    (vault / "SO" / "Escalonamento de processos.pdf").write_text("pdf")
    (vault / "SO" / "Memória virtual.pdf").write_text("pdf")
    (vault / ".obsidian" / "secret escalonamento.json").write_text("{}")
    (outside / "segredo.txt").write_text("x")
    (vault / "fuga").symlink_to(outside)
    monkeypatch.setenv("PEACE_FILE_ROOTS", f"vault={vault}:documentos={docs}")
    return {"vault": vault, "docs": docs, "outside": outside}


@pytest.fixture
def apps(monkeypatch, tmp_path):
    share = tmp_path / "share" / "applications"
    share.mkdir(parents=True)
    for app_id, name, extra in (("md.obsidian.Obsidian", "Obsidian", "StartupWMClass=obsidian\n"), ("kitty", "kitty", ""),
                                ("brave-browser", "Brave", ""), ("secret-tool", "Hidden", "NoDisplay=true\n")):
        (share / f"{app_id}.desktop").write_text(f"[Desktop Entry]\nType=Application\nName={name}\nExec={app_id} %U\n{extra}")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "none"))
    monkeypatch.setattr(actions, "HOME", tmp_path / "home")
    return share


def spawned(monkeypatch, effect=lambda argv: None, code=0):
    calls = []

    def fake(argv, timeout=10):
        calls.append(argv)
        effect(argv)
        return type("P", (), {"returncode": code})()
    monkeypatch.setattr(actions, "_spawn", fake)
    return calls


# --- schemas, permissions, confirmation, audit --------------------------------------------------------

@pytest.mark.parametrize("args", [{"workspace": "3"}, {"workspace": True}, {"workspace": 0}, {"workspace": 100},
                                  {"workspace": 3, "extra": 1}, {}, [3]])
def test_arguments_are_validated_before_anything_runs(hypr, args):
    result = actions.run("workspaces.switch", args)
    assert (result["ok"], result["code"]) == (False, "invalid_arguments") and hypr.sent == []


def test_unknown_tools_and_shell_like_names_are_refused(runtime):
    for name in ("shell", "exec", "files.search; rm -rf ~", None, 3):
        assert actions.run(name, {})["code"] == "unknown_tool"


def test_disabled_categories_are_refused(runtime, roots, monkeypatch):
    monkeypatch.setattr(actions, "ENABLED", {"windows"})
    assert actions.run("files.search", {"query": "so"})["code"] == "permission_denied"


@pytest.mark.parametrize(("name", "args"), [
    ("files.trash", {"query": "Memória"}),
    ("windows.close", {"window": "kitty"}),
    ("browser.close", {"query": "youtube"}),
    ("files.move", {"query": "Memória", "dest_dir": ".", "dest_root": "documentos", "overwrite": True}),
])
def test_actions_that_can_lose_work_need_confirmation(roots, hypr, name, args):
    hypr.add("0xa1", "kitty")
    result = actions.run(name, args)
    assert (result["ok"], result["code"]) == (False, "confirmation_required")
    assert hypr.clients and hypr.sent == [] and (roots["vault"] / "SO" / "Memória virtual.pdf").exists()


def test_audit_records_outcomes_without_arguments(runtime, roots):
    actions.run("files.search", {"query": "Escalonamento secreto"})
    actions.run("files.open", {"path": "../outside/segredo.txt", "root": "vault"})
    text = actions.AUDIT_FILE.read_text()
    assert [json.loads(x)["tool"] for x in text.splitlines()] == ["files.search", "files.open"]
    assert "Escalonamento" not in text and "segredo" not in text
    assert actions.AUDIT_FILE.stat().st_mode & 0o777 == 0o600


def test_a_tool_bug_is_reported_not_raised(runtime, monkeypatch):
    def broken():
        raise KeyError("boom")
    monkeypatch.setitem(actions.TOOLS, "files.roots", actions.Tool("files.roots", "files", "", {}, broken))
    assert actions.run("files.roots", {})["code"] == "internal"


# --- files ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("path", "root"), [
    ("../outside/segredo.txt", "vault"), ("SO/../../outside/segredo.txt", "vault"), (".obsidian/secret escalonamento.json", "vault"),
    ("fuga/segredo.txt", "vault"), ("/etc/passwd", None), ("~/.ssh/id_ed25519", None), ("SO", "home"), ("/abs", "vault"),
])
def test_paths_cannot_leave_the_allowed_folders(runtime, roots, path, root):
    with pytest.raises(actions.ActionError) as e:
        actions.resolve(path, root)
    assert e.value.code == "path_not_allowed"


def test_absolute_paths_inside_a_root_resolve(runtime, roots):
    name, real = actions.resolve(str(roots["vault"] / "SO" / "Memória virtual.pdf"))
    assert name == "vault" and real.name == "Memória virtual.pdf"
    with pytest.raises(actions.ActionError) as e:
        actions.resolve("SO/nada.pdf", "vault")
    assert e.value.code == "not_found"


def test_the_home_folder_is_never_a_root(runtime, monkeypatch):
    monkeypatch.setenv("PEACE_FILE_ROOTS", f"casa={Path.home()}")
    assert actions.file_roots() == {}


def test_search_matches_words_without_accents_and_skips_hidden_folders(runtime, roots):
    result = actions.run("files.search", {"query": "memoria virtual"})
    assert result["ok"] and [i["name"] for i in result["data"]["items"]] == ["Memória virtual.pdf"]
    assert actions.run("files.search", {"query": "escalonamento"})["data"]["items"][0]["path"] == "SO/Escalonamento de processos.pdf"
    assert actions.run("files.search", {"query": "secret"})["data"]["items"] == []


def test_open_uses_the_desktop_launcher_and_reports_whether_a_window_was_seen(roots, hypr, monkeypatch):
    calls = spawned(monkeypatch, lambda argv: hypr.add("0xb2", "org.pwmt.zathura", title="Memória virtual.pdf", focus=0))
    result = actions.run("files.open", {"query": "memoria"})
    assert calls == [["gio", "open", str(roots["vault"] / "SO" / "Memória virtual.pdf")]]
    assert result["ok"] and result["verified"] and result["data"]["window"] == "0xb2"
    spawned(monkeypatch)
    result = actions.run("files.open", {"path": "SO/Escalonamento de processos.pdf", "root": "vault"})
    assert result["ok"] and not result["verified"] and "não confirmei" in result["message"]


def test_open_needs_exactly_one_of_path_or_query(runtime, roots):
    assert actions.run("files.open", {})["code"] == "invalid_arguments"
    assert actions.run("files.open", {"path": "SO", "root": "vault", "query": "x"})["code"] == "invalid_arguments"


def test_move_never_overwrites_without_confirmation_and_trashes_what_it_replaces(runtime, roots, monkeypatch):
    (roots["docs"] / "Memória virtual.pdf").write_text("old")
    args = {"query": "memoria virtual", "root": "vault", "dest_dir": ".", "dest_root": "documentos"}
    assert actions.run("files.move", args)["code"] == "exists"
    trashed = []
    monkeypatch.setattr(actions, "_trash", lambda p: (trashed.append(p.read_text()), p.unlink()))
    result = actions.run("files.move", args | {"overwrite": True}, confirmed=True)
    assert result["ok"] and result["verified"] and trashed == ["old"]
    assert (roots["docs"] / "Memória virtual.pdf").read_text() == "pdf" and not (roots["vault"] / "SO" / "Memória virtual.pdf").exists()


def test_move_refuses_ambiguous_names_roots_and_a_folder_into_itself(runtime, roots):
    assert actions.run("files.move", {"query": "pdf", "dest_dir": ".", "dest_root": "documentos"})["code"] == "ambiguous"
    assert actions.run("files.move", {"path": ".", "root": "vault", "dest_dir": ".", "dest_root": "documentos"})["code"] == "path_not_allowed"
    assert actions.run("files.move", {"path": "SO", "root": "vault", "dest_dir": "SO", "dest_root": "vault"})["code"] == "invalid_arguments"


def test_mkdir_and_trash_stay_inside_roots(runtime, roots, monkeypatch):
    assert actions.run("files.mkdir", {"path": "SO/Resumos", "root": "vault"})["verified"]
    assert actions.run("files.mkdir", {"path": "SO/.escondida", "root": "vault"})["code"] == "path_not_allowed"
    monkeypatch.setattr(actions, "_trash", lambda p: p.rmdir())
    assert actions.run("files.trash", {"path": "SO/Resumos", "root": "vault"}, confirmed=True)["verified"]
    assert actions.run("files.trash", {"path": ".", "root": "vault"}, confirmed=True)["code"] == "path_not_allowed"


# --- applications ----------------------------------------------------------------------------------------

def test_only_visible_installed_apps_can_be_launched(apps, hypr, monkeypatch):
    assert actions.find_app("obsidian")["id"] == "md.obsidian.Obsidian"
    for name, code in (("secret-tool", "not_found"), ("rm -rf", "not_found"), ("i", "ambiguous")):
        assert actions.run("apps.launch", {"app": name})["code"] == code
    calls = spawned(monkeypatch, lambda argv: hypr.add("0xc3", "obsidian"))
    result = actions.run("apps.launch", {"app": "Obsidian"})
    assert calls == [["gio", "launch", str(apps / "md.obsidian.Obsidian.desktop")]] and result["verified"]


def test_a_launch_without_a_window_is_not_reported_as_verified(apps, hypr, monkeypatch):
    hypr.add("0xa1", "kitty", focus=0)  # already open and focused: proves nothing about this launch
    spawned(monkeypatch)
    result = actions.run("apps.launch", {"app": "kitty"})
    assert result["ok"] and not result["verified"]
    hypr.get("0xa1")["focusHistoryID"] = 3
    spawned(monkeypatch, lambda argv: hypr.get("0xa1").update(focusHistoryID=0))  # single instance raised
    assert actions.run("apps.launch", {"app": "kitty"})["verified"]
    spawned(monkeypatch, code=1)
    assert actions.run("apps.launch", {"app": "kitty"})["code"] == "launch_failed"


def test_launchers_run_without_a_shell_in_their_own_scope(runtime, monkeypatch):
    seen = []
    monkeypatch.setattr(actions.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(actions.subprocess, "run", lambda argv, **kw: (seen.append((argv, kw.get("shell"))), type("P", (), {"returncode": 0})())[1])
    actions._spawn(["gio", "open", "/x; rm -rf ~"])
    assert seen == [(["systemd-run", "--user", "--scope", "--collect", "--quiet", "--", "gio", "open", "/x; rm -rf ~"], None)]


# --- Hyprland --------------------------------------------------------------------------------------------

def test_windows_are_listed_from_ipc_json(hypr):
    hypr.add("0xa1", "kitty", ws=1, title="vim notas.md", focus=0)
    hypr.clients.append({"address": "0xdead", "class": "ghost", "mapped": False, "hidden": False, "workspace": {"id": 1}})
    result = actions.run("windows.list", {})
    assert [w["address"] for w in result["data"]["items"]] == ["0xa1"] and result["data"]["items"][0]["focused"]


def test_focus_switch_and_move_are_verified_by_rereading_state(hypr):
    hypr.add("0xa1", "kitty", ws=1, focus=0)
    hypr.add("0xa2", "brave-browser", ws=2, title="Moodle", focus=1)
    assert actions.run("windows.focus", {"window": "moodle"})["verified"]
    assert actions.run("workspaces.switch", {"workspace": 4})["verified"] and hypr.active_ws == 4
    result = actions.run("windows.move_to_workspace", {"window": "kitty", "workspace": 5})
    assert result["verified"] and hypr.get("0xa1")["workspace"]["id"] == 5
    assert hypr.sent[-1] == 'dispatch hl.dsp.window.move({ workspace = 5, window = "address:0xa1", follow = false })'


def test_place_floats_and_uses_the_monitors_usable_area(hypr):
    hypr.add("0xa1", "kitty")
    result = actions.run("windows.place", {"window": "kitty", "x": 50, "y": 0, "width": 50, "height": 100})
    c = hypr.get("0xa1")
    assert result["verified"] and c["floating"] and c["at"] == [960, 40] and c["size"] == [960, 1040]


def test_close_reports_a_window_that_stayed_open(hypr):
    hypr.add("0xa1", "kitty")
    hypr.ignore_close = True
    result = actions.run("windows.close", {"window": "kitty"}, confirmed=True)
    assert result["ok"] and not result["verified"] and "continua aberta" in result["message"]


def test_refused_dispatch_unknown_window_and_old_hyprland(hypr):
    hypr.add("0xa1", "kitty")
    assert actions.run("windows.focus", {"window": "firefox"})["code"] == "not_found"
    hypr.refuse = True
    assert actions.run("windows.focus", {"window": "kitty"})["code"] == "hyprland_refused"
    hypr.version = "v0.54.0"
    actions._hypr.clear()
    assert actions.run("windows.list", {})["code"] == "unsupported"


def test_window_text_never_reaches_lua(hypr):
    hypr.add("0xa1", "kitty", title='x"}) hl.dsp.exec_cmd("rm -rf ~") --')
    actions.run("windows.focus", {"window": 'x"}) hl.dsp.exec_cmd'})
    assert hypr.sent == ['dispatch hl.dsp.focus({ window = "address:0xa1" })']
    for bad in ('a"b', "address:0x1 or 1", "rm -rf", 1.5):
        with pytest.raises(actions.ActionError):
            actions.lua(bad)


def test_a_stale_signature_falls_back_to_the_running_hyprland(hypr, monkeypatch):
    hypr.add("0xa1", "kitty")
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "oldsession")  # Hyprland restarted since the agent started
    assert actions.run("windows.list", {})["data"]["items"][0]["address"] == "0xa1"


def test_no_hyprland_session_is_reported(runtime, monkeypatch):
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE")
    assert actions.run("windows.list", {})["code"] == "hyprland_unavailable"
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "../../etc")
    assert actions.run("windows.list", {})["code"] == "hyprland_unavailable"


# --- layouts -------------------------------------------------------------------------------------------

def test_layout_reuses_windows_launches_missing_apps_and_can_be_undone(apps, hypr, monkeypatch):
    hypr.add("0xa1", "brave-browser", ws=2, floating=True)
    calls = spawned(monkeypatch, lambda argv: hypr.add("0xc3", "obsidian", ws=hypr.active_ws))
    result = actions.run("layouts.apply", {"name": "estudo"})
    assert result["ok"] and result["verified"] and "2 de 2" in result["message"]
    assert calls == [["gio", "launch", str(apps / "md.obsidian.Obsidian.desktop")]]
    assert {hypr.get(a)["workspace"]["id"] for a in ("0xa1", "0xc3")} == {5} and hypr.active_ws == 5
    undo = actions.run("layouts.undo", {})
    assert undo["verified"] and hypr.get("0xa1")["workspace"]["id"] == 2 and hypr.get("0xa1")["floating"]
    assert actions.run("layouts.undo", {})["code"] == "not_found"


def test_layout_partial_failure_is_reported_per_window(apps, hypr, monkeypatch):
    hypr.add("0xa1", "kitty", ws=1)
    spawned(monkeypatch)  # VS Code is not installed here and Brave never shows a window
    result = actions.run("layouts.apply", {"name": "programacao"})
    assert result["ok"] and not result["verified"] and "1 de 3" in result["message"]
    assert "Não encontrei a aplicação «com.microsoft.VSCode»" in result["message"] and "Brave não abriu" in result["message"]


def test_saved_layouts_are_validated_and_overwriting_one_needs_confirmation(apps, hypr):
    hypr.add("0xa1", "kitty", ws=3, floating=True)
    hypr.active_ws = 3
    hypr.get("0xa1")["at"], hypr.get("0xa1")["size"] = [480, 270], [960, 540]
    assert actions.run("layouts.save", {"name": "notas"})["verified"]
    saved = json.loads((actions.LAYOUTS_DIR / "notas.json").read_text())
    assert saved == {"workspace": 3, "windows": [{"app": "kitty", "rect": [25, 25, 50, 50]}]}
    assert actions.run("layouts.save", {"name": "notas"})["code"] == "confirmation_required"
    assert actions.run("layouts.save", {"name": "../x"})["code"] == "invalid_arguments"
    for bad in ('{"workspace": 3}', "not json", '{"workspace": 300, "windows": [{"app": "kitty"}]}',
                '{"workspace": 3, "windows": [{"app": "kitty", "rect": [0, 0, 5, 5]}]}'):
        (actions.LAYOUTS_DIR / "mau.json").write_text(bad)
        assert actions.run("layouts.apply", {"name": "mau"})["code"] == "invalid_layout"


def test_a_tampered_undo_file_is_never_interpolated(hypr):
    hypr.add("0xa1", "kitty", ws=5)
    actions.UNDO_FILE.parent.mkdir(parents=True)
    actions.UNDO_FILE.write_text(json.dumps({"windows": [{"address": '0xa1"}) os.execute("x', "workspace": 1},
                                                         {"address": "0xa1", "workspace": 1, "floating": False}]}))
    result = actions.run("layouts.undo", {})
    assert result["verified"] and hypr.get("0xa1")["workspace"]["id"] == 1
    assert all("execute" not in s for s in hypr.sent)


# --- Brave ---------------------------------------------------------------------------------------------

@pytest.fixture
def bridge(runtime):
    fake = FakeBridge(actions.BROWSER_SOCKET)
    yield fake
    fake.server.shutdown()


def test_tabs_are_found_grouped_and_closed_through_the_bridge(bridge):
    assert [t["id"] for t in actions.run("browser.tabs", {"query": "escalonamento"})["data"]["items"]] == [3]
    assert [t["id"] for t in actions.run("browser.tabs", {"query": "sistemas operativos"})["data"]["items"]] == [1]
    grouped = actions.run("browser.group", {"query": "escalonamento", "title": "SO", "color": "blue"})
    group = next(r for r in bridge.requests if r["op"] == "group")
    assert grouped["verified"] and group["args"] == {"tabIds": [3], "title": "SO", "color": "blue"}
    closed = actions.run("browser.close", {"query": "youtube"}, confirmed=True)
    assert closed["verified"] and [t["id"] for t in bridge.tabs] == [1, 3]
    assert actions.run("browser.focus", {"query": "escalonamento"})["verified"]


def test_open_tab_accepts_only_web_addresses(bridge):
    assert actions.run("browser.open", {"url": "https://moodle.example/course/view.php?id=4", "group": "SO"})["verified"]
    for url in ("javascript:alert(1)", "file:///etc/passwd", "chrome://settings", "https://x y"):
        assert actions.run("browser.open", {"url": url})["code"] == "invalid_arguments"


def test_a_missing_or_confused_bridge_is_reported(bridge):
    bridge.wrong_id = True
    assert actions.run("browser.tabs", {})["code"] == "browser_error"
    bridge.server.shutdown()
    actions.BROWSER_SOCKET.unlink()
    assert actions.run("browser.tabs", {})["code"] == "browser_unavailable"


# --- plans -----------------------------------------------------------------------------------------------

def test_a_plan_keeps_going_after_a_failure_and_stops_when_cancelled(hypr):
    hypr.add("0xa1", "kitty")
    steps = [{"tool": "workspaces.switch", "args": {"workspace": 3}}, {"tool": "windows.focus", "args": {"window": "nada"}},
             "not a step", {"tool": "workspaces.switch", "args": {"workspace": 4}}]
    seen = []
    results, cancelled = actions.run_plan(steps, False, "j1", progress=lambda r: seen.append(len(r)))
    assert [r["ok"] for r in results] == [True, False, False, True] and not cancelled and seen == [1, 2, 3, 4]
    hypr.sent.clear()
    results, cancelled = actions.run_plan(steps, False, "j2", cancelled=lambda: len(hypr.sent) >= 1)
    assert len(results) == 1 and cancelled
    assert len(actions.run_plan([steps[0]] * 20, False)[0]) == actions.MAX_STEPS

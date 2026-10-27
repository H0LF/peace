"""Desktop actions PEACE may carry out on this PC, and nothing else.

Every action is a named tool with an explicit argument schema. The assistant (keyword rules or the local
model) can only pick tools from this table, and the arguments are validated here again whatever the server
sent: no tool runs a shell command, file tools stay inside the allowed folders, and anything that could lose
work (trash, overwrite, closing windows or tabs) needs a confirmation the user gave. Results say what was
verified by re-reading the desktop state, not what was attempted.

Hyprland is driven over its own IPC socket with the Lua dispatchers of 0.56+ (`hl.dsp.*`); Brave through
the PEACE extension and its native-messaging host (desktop/brave/), which listens on a private socket.
Standard library only, like the agent.
"""

import configparser
import json
import os
import re
import shutil
import socket
import subprocess
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

HOME = Path.home()
VAULT = Path(os.environ.get("PEACE_VAULT", "~/Peace Vault")).expanduser()
STATE_DIR = Path(os.environ.get("PEACE_STATE", "~/.local/state/peace/materials.json")).expanduser().parent
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "peace"
LAYOUTS_DIR = CONFIG_DIR / "layouts"
UNDO_FILE = STATE_DIR / "layout-undo.json"
AUDIT_FILE = STATE_DIR / "actions.jsonl"
AUDIT_MAX_BYTES = 512 * 1024
RUNTIME = Path(os.environ["XDG_RUNTIME_DIR"]) if os.environ.get("XDG_RUNTIME_DIR") else None
BROWSER_SOCKET = RUNTIME / "peace-browser.sock" if RUNTIME else None
CATEGORIES = ("files", "apps", "windows", "browser", "layouts")
# Turn categories off in desktop.env, e.g. PEACE_ACTIONS=files,windows (empty: all off).
ENABLED = set(CATEGORIES) if "PEACE_ACTIONS" not in os.environ else {
    c.strip() for c in os.environ["PEACE_ACTIONS"].split(",") if c.strip() in CATEGORIES}
LAUNCH_WAIT = 10.0  # seconds to wait for a launched app's window
OPEN_WAIT = 6.0  # and for a file's window (the app may already be running)
MIN_HYPRLAND = (0, 56)  # the Lua dispatchers (hl.dsp.*) used below

ADDRESS = re.compile(r"^0x[0-9a-f]{1,16}$")
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
GROUP_COLORS = ("grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange")


class ActionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


# --- schemas -----------------------------------------------------------------------------------------

def validate(schema: dict[str, dict], args: Any) -> dict[str, Any]:
    """Arguments checked against a small JSON-schema subset: type, required, enum, bounds, pattern, items."""
    if not isinstance(args, dict):
        raise ActionError("invalid_arguments", "Argumentos inválidos.")
    unknown = set(args) - set(schema)
    if unknown:
        raise ActionError("invalid_arguments", f"Argumento desconhecido: {sorted(map(str, unknown))[0][:40]}.")
    out = {}
    for key, rule in schema.items():
        if key not in args or args[key] is None:
            if rule.get("required"):
                raise ActionError("invalid_arguments", f"Falta o argumento {key}.")
            if "default" in rule:
                out[key] = rule["default"]
            continue
        out[key] = _check(key, rule, args[key])
    return out


def _check(key: str, rule: dict, value: Any) -> Any:
    bad = ActionError("invalid_arguments", f"Valor inválido para {key}.")
    kind = rule["type"]
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise bad
        if not rule.get("minimum", value) <= value <= rule.get("maximum", value):
            raise bad
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise bad
    elif kind == "string":
        if not isinstance(value, str) or "\x00" in value or not rule.get("minLength", 1) <= len(value) <= rule.get("maxLength", 200):
            raise bad
        if "pattern" in rule and not re.fullmatch(rule["pattern"], value):
            raise bad
    elif kind == "array":
        if not isinstance(value, list) or not 1 <= len(value) <= rule.get("maxItems", 20):
            raise bad
        value = [_check(key, rule["items"], v) for v in value]
    elif kind == "object":
        if not isinstance(value, dict):
            raise bad
    if "enum" in rule and value not in rule["enum"]:
        raise bad
    return value


def _str(max_length: int = 200, **kw) -> dict:
    return {"type": "string", "maxLength": max_length, **kw}


def _int(minimum: int, maximum: int, **kw) -> dict:
    return {"type": "integer", "minimum": minimum, "maximum": maximum, **kw}


@dataclass
class Tool:
    name: str
    category: str
    summary: str
    params: dict[str, dict]
    fn: Callable[..., dict]
    destructive: Callable[[dict], bool] = field(default=lambda args: False)


TOOLS: dict[str, Tool] = {}


def tool(name: str, category: str, summary: str, params: dict[str, dict], destructive: Callable[[dict], bool] | bool = False):
    def register(fn: Callable[..., dict]) -> Callable[..., dict]:
        TOOLS[name] = Tool(name, category, summary, params, fn,
                           destructive if callable(destructive) else (lambda args, d=destructive: d))
        return fn
    return register


def done(message: str, verified: bool = True, **data: Any) -> dict:
    return {"message": message, "verified": verified, "data": data}


# --- files ----------------------------------------------------------------------------------------------

def file_roots() -> dict[str, Path]:
    """Folders file tools may touch. PEACE_FILE_ROOTS="name=/path:name2=~/path" replaces the defaults."""
    if spec := os.environ.get("PEACE_FILE_ROOTS"):
        pairs = [p.split("=", 1) for p in spec.split(":") if "=" in p]
        roots = {n.strip(): Path(p).expanduser() for n, p in pairs if NAME.fullmatch(n.strip())}
    else:
        roots = {"vault": VAULT, "documentos": HOME / "Documents", "transferencias": HOME / "Downloads",
                 "secretaria": HOME / "Desktop", "projetos": HOME / "Projects"}
    # The home folder itself is never a root: it holds .ssh, .config and the PEACE secrets.
    return {n: p.resolve() for n, p in roots.items() if p.is_absolute() and p.is_dir() and p.resolve() != HOME.resolve()}


def _relative_parts(path: str) -> tuple[str, ...]:
    parts = Path(path).parts
    if any(p in ("..", ".") or p.startswith(".") for p in parts):
        raise ActionError("path_not_allowed", "Caminho com pastas ocultas ou «..» não é permitido.")
    return parts


def resolve(path: str, root: str | None = None, must_exist: bool = True) -> tuple[str, Path]:
    """(root name, real path) for a path inside an allowed folder; symlinks may not lead out of it."""
    roots = file_roots()
    if root is not None:
        if root not in roots:
            raise ActionError("path_not_allowed", f"A pasta «{root}» não está autorizada.")
        if Path(path).is_absolute() or path.startswith("~"):
            raise ActionError("path_not_allowed", "Com uma pasta raiz, o caminho tem de ser relativo.")
        name, base, rel = root, roots[root], path
    else:
        if ".." in Path(path).parts:
            raise ActionError("path_not_allowed", "Caminho com pastas ocultas ou «..» não é permitido.")
        absolute = Path(os.path.normpath(Path(path).expanduser()))
        if not absolute.is_absolute():
            raise ActionError("path_not_allowed", "Indica a pasta raiz ou um caminho absoluto.")
        match = next(((n, b) for n, b in roots.items() if absolute.is_relative_to(b)), None)
        if not match:
            raise ActionError("path_not_allowed", "Fora das pastas autorizadas.")
        name, base = match
        rel = str(absolute.relative_to(base))
    parts = _relative_parts(rel) if rel not in ("", ".") else ()
    target = base.joinpath(*parts)
    try:
        real = target.resolve(strict=must_exist)
    except (FileNotFoundError, NotADirectoryError):
        raise ActionError("not_found", "O ficheiro ou pasta não existe.") from None
    except (OSError, RuntimeError):
        raise ActionError("path_not_allowed", "Caminho inválido.") from None
    if not real.is_relative_to(base):
        raise ActionError("path_not_allowed", "O caminho sai da pasta autorizada.")
    return name, real


def _display(path: Path) -> str:
    return "~/" + str(path.relative_to(HOME)) if path.is_relative_to(HOME) else str(path)


def _new_name(name: str) -> str:
    if not name or "/" in name or name.startswith(".") or len(name.encode()) > 255:
        raise ActionError("path_not_allowed", "Nome inválido.")
    return name


def _spawn(argv: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    """Run a launcher in its own systemd scope, so apps survive an agent restart (no shell involved)."""
    if shutil.which("systemd-run"):
        argv = ["systemd-run", "--user", "--scope", "--collect", "--quiet", "--", *argv]
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ActionError("timeout", "O lançador não respondeu a tempo.") from None
    except OSError:
        raise ActionError("unavailable", "Lançador indisponível.") from None


ROOT = _str(40, pattern=NAME.pattern)
PATH_ARGS = {"path": _str(1000, required=True), "root": ROOT}
# Either an exact path or a name to search for (the assistant knows names, not this PC's folders).
TARGET_ARGS = {"path": _str(1000), "root": ROOT, "query": _str(100)}


def _target(path: str | None, root: str | None, query: str | None, unique: bool) -> Path:
    if (path is None) == (query is None):
        raise ActionError("invalid_arguments", "Indica um caminho ou um nome a procurar.")
    if path is not None:
        return resolve(path, root)[1]
    items = files_search(query=query, limit=10, root=root)["data"]["items"]
    if not items:
        raise ActionError("not_found", f"Não encontrei nenhum ficheiro «{query}».")
    exact = [i for i in items if _fold(Path(i["name"]).stem) == _fold(query) or _fold(i["name"]) == _fold(query)]
    if unique and len(exact or items) > 1:
        raise ActionError("ambiguous", "Vários ficheiros correspondem: " + ", ".join(i["name"] for i in items[:5]) + ".")
    best = (exact or sorted(items, key=lambda i: len(i["name"])))[0]
    return resolve(best["path"], best["root"])[1]


@tool("files.roots", "files", "Pastas autorizadas.", {})
def files_roots() -> dict:
    roots = file_roots()
    return done(f"{len(roots)} pastas autorizadas.", items=[{"name": n, "path": _display(p)} for n, p in roots.items()])


@tool("files.search", "files", "Procurar ficheiros pelo nome nas pastas autorizadas.",
      {"query": _str(100, required=True), "root": ROOT, "limit": _int(1, 50, default=20)})
def files_search(query: str, limit: int, root: str | None = None) -> dict:
    words = _fold(query).split()
    if not words:
        raise ActionError("invalid_arguments", "Pesquisa vazia.")
    roots = file_roots()
    if root is not None and root not in roots:
        raise ActionError("path_not_allowed", f"A pasta «{root}» não está autorizada.")
    found, scanned, deadline = [], 0, time.monotonic() + 3
    for name, base in ((root, roots[root]),) if root else roots.items():
        stack = [base]
        while stack and len(found) < limit and scanned < 50_000 and time.monotonic() < deadline:
            try:
                entries = list(os.scandir(stack.pop()))
            except OSError:
                continue
            for e in entries:
                scanned += 1
                if e.name.startswith("."):
                    continue
                if e.is_dir(follow_symlinks=False):
                    stack.append(Path(e.path))
                if all(w in _fold(e.name) for w in words) and len(found) < limit:
                    found.append({"root": name, "path": str(Path(e.path).relative_to(base)), "name": e.name,
                                  "kind": "pasta" if e.is_dir(follow_symlinks=False) else "ficheiro"})
    complete = scanned < 50_000 and time.monotonic() < deadline
    return done(f"Encontrei {len(found)} resultado(s)" + ("." if complete else " (pesquisa parcial: pastas muito grandes)."),
                items=found, complete=complete)


@tool("files.list", "files", "Listar o conteúdo de uma pasta autorizada.", PATH_ARGS)
def files_list(path: str, root: str | None = None) -> dict:
    _, folder = resolve(path, root)
    if not folder.is_dir():
        raise ActionError("not_a_directory", "Não é uma pasta.")
    items = sorted(({"name": e.name, "kind": "pasta" if e.is_dir() else "ficheiro"} for e in os.scandir(folder)
                    if not e.name.startswith(".")), key=lambda i: (i["kind"], i["name"].lower()))
    return done(f"{len(items)} itens em {folder.name}.", items=items[:200])


def _windows_snapshot() -> list[dict] | None:
    try:
        return hyprland().clients()
    except ActionError:
        return None


def _new_window(before: list[dict] | None, match: Callable[[dict], bool], wait: float) -> dict | None:
    """A new window that matches, or a matching one that only now took focus (a single-instance app that was
    asked again), polled until `wait` runs out. A window that was already focused proves nothing."""
    if before is None:
        return None
    known = {c["address"] for c in before}
    focused = {c["address"] for c in before if c.get("focusHistoryID") == 0}
    deadline = time.monotonic() + wait
    while True:
        now = _windows_snapshot() or []
        hit = next((c for c in now if c["address"] not in known and match(c)), None) or \
            next((c for c in now if c["address"] in known - focused and match(c) and c.get("focusHistoryID") == 0), None)
        if hit or time.monotonic() >= deadline:
            return hit
        time.sleep(0.25)


@tool("files.open", "files", "Abrir um ficheiro ou pasta autorizado na aplicação predefinida.", TARGET_ARGS)
def files_open(path: str | None = None, root: str | None = None, query: str | None = None) -> dict:
    target = _target(path, root, query, unique=False)
    before = _windows_snapshot()
    if _spawn(["gio", "open", str(target)]).returncode:
        raise ActionError("open_failed", "Nenhuma aplicação aceitou abrir o ficheiro.")
    stem = _fold(target.stem)[:40]
    window = _new_window(before, lambda c: stem in _fold(c.get("title", "")), OPEN_WAIT)
    if window:
        return done(f"Abri «{target.name}».", window=window["address"])
    return done(f"Pedi para abrir «{target.name}», mas não confirmei a janela.", verified=False)


@tool("files.mkdir", "files", "Criar uma pasta dentro de uma pasta autorizada.", PATH_ARGS)
def files_mkdir(path: str, root: str | None = None) -> dict:
    parent_path = str(Path(path).parent) if root else str(Path(path).expanduser().parent)
    _, parent = resolve(parent_path, root)
    target = parent / _new_name(Path(path).name)
    if target.exists():
        return done(f"A pasta «{target.name}» já existia.", verified=target.is_dir())
    target.mkdir()
    return done(f"Criei a pasta «{target.name}».", verified=target.is_dir())


@tool("files.move", "files", "Mover ou mudar o nome de um ficheiro entre pastas autorizadas (nunca apaga).",
      {**TARGET_ARGS, "dest_dir": _str(1000, required=True), "dest_root": ROOT,
       "new_name": _str(255), "overwrite": {"type": "boolean", "default": False}},
      destructive=lambda args: bool(args.get("overwrite")))
def files_move(dest_dir: str, overwrite: bool, path: str | None = None, root: str | None = None, query: str | None = None,
               dest_root: str | None = None, new_name: str | None = None) -> dict:
    source = _target(path, root, query, unique=True)
    if source in file_roots().values():
        raise ActionError("path_not_allowed", "Não movo uma pasta raiz.")
    _, folder = resolve(dest_dir, dest_root)
    if not folder.is_dir():
        raise ActionError("not_a_directory", "O destino não é uma pasta.")
    target = folder / _new_name(new_name or source.name)
    if source.is_dir() and target.is_relative_to(source):
        raise ActionError("invalid_arguments", "Não é possível mover uma pasta para dentro dela própria.")
    if target == source:
        return done(f"«{source.name}» já está aí.")
    if target.exists():
        if not overwrite:
            raise ActionError("exists", f"Já existe «{target.name}» no destino.")
        _trash(target)  # the replaced file goes to the trash, never straight to deletion
        if target.exists():
            raise ActionError("exists", f"Não consegui libertar «{target.name}» no destino.")
    shutil.move(source, target)
    return done(f"Movi «{source.name}» para {_display(folder)}.", verified=target.exists() and not source.exists())


def _trash(target: Path) -> None:
    try:
        proc = subprocess.run(["gio", "trash", "--", str(target)], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise ActionError("trash_failed", "Não consegui pôr no lixo.") from None
    if proc.returncode:
        raise ActionError("trash_failed", "Não consegui pôr no lixo.")


@tool("files.trash", "files", "Pôr um ficheiro ou pasta no lixo (recuperável).", TARGET_ARGS, destructive=True)
def files_trash(path: str | None = None, root: str | None = None, query: str | None = None) -> dict:
    target = _target(path, root, query, unique=True)
    if target in file_roots().values():
        raise ActionError("path_not_allowed", "Não ponho uma pasta raiz no lixo.")
    _trash(target)
    return done(f"Pus «{target.name}» no lixo (recuperável).", verified=not target.exists())


# --- applications -------------------------------------------------------------------------------------

def applications() -> dict[str, dict]:
    """Installed, visible desktop entries by id (e.g. "kitty", "md.obsidian.Obsidian"); earlier dirs win."""
    dirs = [Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share")), HOME / ".local/share/flatpak/exports/share"]
    dirs += [Path(d) for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if d]
    dirs.append(Path("/var/lib/flatpak/exports/share"))
    apps: dict[str, dict] = {}
    for d in dirs:
        folder = d / "applications"
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.desktop")):
            if f.stem in apps:
                continue
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            try:
                parser.read(f, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            apps[f.stem] = {"id": f.stem, "name": entry.get("Name", f.stem), "path": str(f),
                            "wm_class": entry.get("StartupWMClass", ""),
                            "hidden": entry.get("NoDisplay") == "true" or entry.get("Hidden") == "true"
                            or entry.get("Type", "Application") != "Application"}
    return {k: v for k, v in apps.items() if not v["hidden"]}


def find_app(query: str) -> dict:
    apps = applications()
    if query in apps:
        return apps[query]
    q = _fold(query).strip()
    exact = [a for a in apps.values() if q in (_fold(a["name"]), _fold(a["id"]), _fold(a["id"].split(".")[-1]))]
    partial = [a for a in apps.values() if q and (q in _fold(a["name"]) or q in _fold(a["id"]))]
    hits = exact or partial
    if not hits:
        raise ActionError("not_found", f"Não encontrei a aplicação «{query}».")
    if len(hits) > 1:
        raise ActionError("ambiguous", "Várias aplicações correspondem: " + ", ".join(sorted(a["name"] for a in hits)[:5]) + ".")
    return hits[0]


def _app_window(app: dict) -> Callable[[dict], bool]:
    names = {_fold(n) for n in (app["wm_class"], app["id"], app["id"].split(".")[-1], app["name"]) if n}
    return lambda c: _fold(c.get("class", "")) in names or _fold(c.get("initialClass", "")) in names


@tool("apps.list", "apps", "Aplicações instaladas.", {})
def apps_list() -> dict:
    apps = applications()
    return done(f"{len(apps)} aplicações.", items=[{"id": a["id"], "name": a["name"]} for a in apps.values()][:300])


@tool("apps.launch", "apps", "Abrir uma aplicação instalada, opcionalmente com ficheiros autorizados.",
      {"app": _str(100, required=True), "files": {"type": "array", "maxItems": 10, "items": _str(1000)}, "root": ROOT})
def apps_launch(app: str, files: list[str] | None = None, root: str | None = None) -> dict:
    entry = find_app(app)
    paths = [str(resolve(f, root)[1]) for f in files or []]
    before = _windows_snapshot()
    if _spawn(["gio", "launch", entry["path"], *paths]).returncode:
        raise ActionError("launch_failed", f"Não consegui abrir {entry['name']}.")
    window = _new_window(before, _app_window(entry), LAUNCH_WAIT)
    if window:
        return done(f"Abri {entry['name']}.", window=window["address"], app=entry["id"])
    return done(f"Pedi para abrir {entry['name']}, mas não vi a janela aparecer.", verified=False, app=entry["id"])


# --- Hyprland --------------------------------------------------------------------------------------------

class Hyprland:
    """Hyprland's request socket: `j/<query>` returns JSON, `dispatch <hl.dsp…>` runs one Lua dispatcher."""

    def __init__(self, path: Path):
        self.path = path

    def request(self, command: str, timeout: float = 3) -> str:
        try:
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(timeout)
                s.connect(str(self.path))
                s.sendall(command.encode())
                chunks = []
                while chunk := s.recv(65536):
                    chunks.append(chunk)
        except OSError:
            raise ActionError("hyprland_unavailable", "O Hyprland não respondeu.") from None
        return b"".join(chunks).decode(errors="replace")

    def query(self, what: str) -> Any:
        try:
            return json.loads(self.request(f"j/{what}"))
        except ValueError:
            raise ActionError("hyprland_unavailable", "Resposta inválida do Hyprland.") from None

    def clients(self) -> list[dict]:
        return [c for c in self.query("clients") if isinstance(c, dict) and c.get("mapped") and not c.get("hidden")
                and ADDRESS.fullmatch(str(c.get("address", ""))) and isinstance(c.get("workspace"), dict)]

    def dispatch(self, dispatcher: str) -> None:
        # A dispatch aimed at a missing window can still answer "ok": callers verify by re-reading state.
        if self.request(f"dispatch {dispatcher}").strip() != "ok":
            raise ActionError("hyprland_refused", "O Hyprland recusou a ação.")

    def check_version(self) -> None:
        tag = str(self.query("version").get("tag", ""))
        m = re.match(r"v?(\d+)\.(\d+)", tag)
        if not m or (int(m[1]), int(m[2])) < MIN_HYPRLAND:
            raise ActionError("unsupported", f"Hyprland {tag or '?'} não suportado (é preciso 0.56 ou mais recente).")


_hypr: dict[str, Hyprland] = {}


def hyprland() -> Hyprland:
    """The session's Hyprland; after a compositor restart the agent's signature is stale, so the newest live one."""
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    folder = RUNTIME / "hypr" if RUNTIME else None
    if folder and re.fullmatch(r"[A-Za-z0-9_]+", sig) and (folder / sig / ".socket.sock").exists():
        path = folder / sig / ".socket.sock"
    else:
        live = sorted((p for p in folder.glob("*/.socket.sock") if re.fullmatch(r"[A-Za-z0-9_]+", p.parent.name)),
                      key=lambda p: p.stat().st_mtime) if folder and folder.is_dir() else []
        if not live:
            raise ActionError("hyprland_unavailable", "Não há uma sessão Hyprland.")
        path = live[-1]
    if str(path) not in _hypr:
        h = Hyprland(path)
        h.check_version()
        _hypr[str(path)] = h
    return _hypr[str(path)]


def lua(value: Any) -> str:
    """A Lua literal for an already-validated value; anything that could carry code is refused."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"address:0x[0-9a-f]{1,16}|[a-z]{1,10}", value):
        return f'"{value}"'
    raise ActionError("invalid_arguments", "Valor recusado para o Hyprland.")


def dsp(name: str, **args: Any) -> str:
    return f"hl.dsp.{name}({{ {', '.join(f'{k} = {lua(v)}' for k, v in args.items())} }})"


def _public(c: dict) -> dict:
    return {"address": c["address"], "class": c.get("class", ""), "title": str(c.get("title", ""))[:120],
            "workspace": c["workspace"].get("id"), "floating": bool(c.get("floating")),
            "at": c.get("at"), "size": c.get("size"), "focused": c.get("focusHistoryID") == 0}


def find_window(clients: list[dict], query: str) -> dict:
    """By address, then exact class, then words of the class or title; the most recently used wins."""
    q = _fold(query).strip()
    if ADDRESS.fullmatch(q):
        hits = [c for c in clients if c["address"] == q]
    else:
        hits = [c for c in clients if q in (_fold(c.get("class", "")), _fold(c.get("initialClass", "")))]
        hits = hits or [c for c in clients if q and (q in _fold(c.get("class", "")) or q in _fold(str(c.get("title", ""))))]
    if not hits:
        raise ActionError("not_found", f"Não encontrei uma janela «{query}».")
    return min(hits, key=lambda c: c.get("focusHistoryID", 99))


def _wait_for(check: Callable[[], bool], seconds: float = 2) -> bool:
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def _client(address: str) -> dict | None:
    return next((c for c in hyprland().clients() if c["address"] == address), None)


WINDOW = _str(200, required=True)
WORKSPACE = _int(1, 99, required=True)


@tool("windows.list", "windows", "Janelas abertas.", {})
def windows_list() -> dict:
    clients = hyprland().clients()
    return done(f"{len(clients)} janelas abertas.", items=[_public(c) for c in clients])


@tool("windows.focus", "windows", "Dar foco a uma janela.", {"window": WINDOW})
def windows_focus(window: str) -> dict:
    h = hyprland()
    c = find_window(h.clients(), window)
    h.dispatch(dsp("focus", window=f"address:{c['address']}"))
    ok = _wait_for(lambda: h.query("activewindow").get("address") == c["address"])
    return done(f"Foco em {c.get('class') or 'janela'}.", verified=ok)


def move_to_workspace(h: Hyprland, address: str, workspace: int) -> bool:
    """Silent move: the user stays where they are."""
    h.dispatch(dsp("window.move", workspace=workspace, window=f"address:{address}", follow=False))
    return _wait_for(lambda: (_client(address) or {}).get("workspace", {}).get("id") == workspace)


@tool("windows.move_to_workspace", "windows", "Mover uma janela para outra área de trabalho.",
      {"window": WINDOW, "workspace": WORKSPACE})
def windows_move_to_workspace(window: str, workspace: int) -> dict:
    h = hyprland()
    c = find_window(h.clients(), window)
    ok = move_to_workspace(h, c["address"], workspace)
    return done(f"Movi {c.get('class') or 'a janela'} para a área {workspace}.", verified=ok)


def _monitor(h: Hyprland, c: dict) -> dict:
    mon = next((m for m in h.query("monitors") if m.get("id") == c.get("monitor")), None)
    if not mon:
        raise ActionError("not_found", "Monitor da janela não encontrado.")
    return mon


def place(h: Hyprland, c: dict, x: int, y: int, width: int, height: int) -> bool:
    """Float a window and put it at a share (percent) of its monitor's usable area."""
    mon = _monitor(h, c)
    scale = mon.get("scale") or 1
    left, top, right, bottom = (mon.get("reserved") or [0, 0, 0, 0])[:4]
    usable_w, usable_h = mon["width"] / scale - left - right, mon["height"] / scale - top - bottom
    px, py = round(mon["x"] + left + usable_w * x / 100), round(mon["y"] + top + usable_h * y / 100)
    pw, ph = round(usable_w * width / 100), round(usable_h * height / 100)
    target = f"address:{c['address']}"
    if not c.get("floating"):
        h.dispatch(dsp("window.float", action="set", window=target))
    h.dispatch(dsp("window.resize", x=pw, y=ph, window=target))
    h.dispatch(dsp("window.move", x=px, y=py, window=target))

    def landed() -> bool:
        now = _client(c["address"]) or {}
        return bool(now.get("floating")) and all(abs(a - b) <= 10 for a, b in zip([*now.get("at", []), *now.get("size", [])], [px, py, pw, ph]))
    return _wait_for(landed)


PERCENT = {k: _int(0, 90, required=True) for k in ("x", "y")} | {k: _int(10, 100, required=True) for k in ("width", "height")}


@tool("windows.place", "windows", "Pôr uma janela flutuante numa posição e tamanho (percentagem do monitor).",
      {"window": WINDOW, **PERCENT})
def windows_place(window: str, x: int, y: int, width: int, height: int) -> dict:
    h = hyprland()
    c = find_window(h.clients(), window)
    return done(f"Arrumei {c.get('class') or 'a janela'}.", verified=place(h, c, x, y, width, height))


@tool("windows.close", "windows", "Fechar uma janela (pode perder trabalho não guardado).", {"window": WINDOW}, destructive=True)
def windows_close(window: str) -> dict:
    h = hyprland()
    c = find_window(h.clients(), window)
    h.dispatch(dsp("window.close", window=f"address:{c['address']}"))
    gone = _wait_for(lambda: _client(c["address"]) is None, 3)
    return done(f"Fechei {c.get('class') or 'a janela'}." if gone else
                f"Pedi para fechar {c.get('class') or 'a janela'}, mas continua aberta (talvez peça para guardar).", verified=gone)


@tool("workspaces.list", "windows", "Áreas de trabalho.", {})
def workspaces_list() -> dict:
    h = hyprland()
    active = h.query("activeworkspace").get("id")
    items = [{"id": w["id"], "name": w.get("name"), "windows": w.get("windows", 0), "active": w["id"] == active}
             for w in h.query("workspaces") if isinstance(w, dict) and isinstance(w.get("id"), int)]
    return done(f"{len(items)} áreas de trabalho; ativa: {active}.", items=items)


def switch_workspace(h: Hyprland, workspace: int) -> bool:
    h.dispatch(dsp("focus", workspace=workspace))
    return _wait_for(lambda: h.query("activeworkspace").get("id") == workspace)


@tool("workspaces.switch", "windows", "Mudar para uma área de trabalho.", {"workspace": WORKSPACE})
def workspaces_switch(workspace: int) -> dict:
    return done(f"Estou na área de trabalho {workspace}.", verified=switch_workspace(hyprland(), workspace))


# --- layouts ---------------------------------------------------------------------------------------------

BUILTIN_LAYOUTS = {
    "estudo": {"workspace": 5, "windows": [{"app": "md.obsidian.Obsidian"}, {"app": "brave-browser"}]},
    "programacao": {"workspace": 6, "windows": [{"app": "com.microsoft.VSCode"}, {"app": "kitty"}, {"app": "brave-browser"}]},
}
SLOT_SCHEMA = {"app": _str(100, required=True), "rect": {"type": "array", "maxItems": 4, "items": _int(0, 100)}}


def load_layout(name: str) -> dict:
    """A saved layout (~/.config/peace/layouts/<name>.json) or a built-in one, validated like tool input."""
    if not NAME.fullmatch(name):
        raise ActionError("invalid_arguments", "Nome de disposição inválido.")
    path = LAYOUTS_DIR / f"{name}.json"
    try:
        raw = json.loads(path.read_text()) if path.exists() else BUILTIN_LAYOUTS.get(name)
    except (OSError, ValueError):
        raise ActionError("invalid_layout", f"A disposição «{name}» está corrompida.") from None
    if raw is None:
        raise ActionError("not_found", f"Não existe a disposição «{name}».")
    if not isinstance(raw, dict) or not isinstance(raw.get("windows"), list) or not 1 <= len(raw["windows"]) <= 8:
        raise ActionError("invalid_layout", f"A disposição «{name}» é inválida.")
    try:
        workspace = _check("workspace", WORKSPACE, raw.get("workspace"))
        slots = [validate(SLOT_SCHEMA, s) for s in raw["windows"]]
    except ActionError:
        raise ActionError("invalid_layout", f"A disposição «{name}» é inválida.") from None
    if any("rect" in s and (len(s["rect"]) != 4 or s["rect"][2] < 10 or s["rect"][3] < 10) for s in slots):
        raise ActionError("invalid_layout", f"A disposição «{name}» tem uma posição inválida.")
    return {"workspace": workspace, "windows": slots}


@tool("layouts.list", "layouts", "Disposições de janelas guardadas.", {})
def layouts_list() -> dict:
    saved = {p.stem for p in LAYOUTS_DIR.glob("*.json") if NAME.fullmatch(p.stem)} if LAYOUTS_DIR.is_dir() else set()
    names = sorted(set(BUILTIN_LAYOUTS) | saved)
    return done(f"{len(names)} disposições.", items=[{"name": n} for n in names])


@tool("layouts.apply", "layouts", "Abrir e arrumar as janelas de uma disposição na sua área de trabalho (reversível).",
      {"name": _str(40, required=True, pattern=NAME.pattern)})
def layouts_apply(name: str) -> dict:
    layout = load_layout(name)
    h = hyprland()
    ws, used, snapshot, steps = layout["workspace"], set(), [], []
    for slot in layout["windows"]:
        try:
            app = find_app(slot["app"])
            match = _app_window(app)
            clients = h.clients()
            # Reuse a window already there, then one elsewhere; launch only when the app has none.
            c = next((c for c in sorted(clients, key=lambda c: (c["workspace"].get("id") != ws, c.get("focusHistoryID", 99)))
                      if c["address"] not in used and match(c)), None)
            if c is None:
                if _spawn(["gio", "launch", app["path"]]).returncode:
                    raise ActionError("launch_failed", f"Não consegui abrir {app['name']}.")
                c = _new_window(clients, lambda x: match(x) and x["address"] not in used, LAUNCH_WAIT)
                if c is None:
                    raise ActionError("not_verified", f"{app['name']} não abriu uma janela a tempo.")
            used.add(c["address"])
            snapshot.append({"address": c["address"], "workspace": c["workspace"].get("id"),
                             "floating": bool(c.get("floating")), "at": c.get("at"), "size": c.get("size")})
            ok = c["workspace"].get("id") == ws or move_to_workspace(h, c["address"], ws)
            if ok and "rect" in slot:
                ok = place(h, _client(c["address"]) or c, *slot["rect"])
            steps.append({"app": app["name"], "ok": ok})
        except ActionError as e:
            steps.append({"app": slot["app"], "ok": False, "error": e.message})
    if snapshot:
        _save_undo(snapshot)
    active = switch_workspace(h, ws)
    placed = sum(s["ok"] for s in steps)
    message = f"Disposição «{name}»: {placed} de {len(steps)} janelas na área {ws}."
    failed = [s.get("error") or f"{s['app']} não ficou na área" for s in steps if not s["ok"]]
    if failed:
        message += " Falhou: " + "; ".join(failed) + "."
    if placed == 0:
        raise ActionError("layout_failed", message)
    return done(message, verified=active and placed == len(steps), steps=steps)


def _save_undo(snapshot: list[dict]) -> None:
    UNDO_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = UNDO_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": time.time(), "windows": snapshot}))
    os.replace(tmp, UNDO_FILE)


def _pair(value: Any) -> list[int] | None:
    return value if isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) and 0 <= v < 100_000 for v in value) else None


@tool("layouts.undo", "layouts", "Repor as janelas como estavam antes da última disposição.", {})
def layouts_undo() -> dict:
    try:
        saved = json.loads(UNDO_FILE.read_text())["windows"]
        assert isinstance(saved, list)
    except (OSError, ValueError, KeyError, TypeError, AssertionError):
        raise ActionError("not_found", "Não há nenhuma disposição para desfazer.") from None
    h = hyprland()
    restored, missing, total = 0, 0, 0
    for w in saved:
        if not isinstance(w, dict) or not ADDRESS.fullmatch(str(w.get("address"))) or not isinstance(w.get("workspace"), int) \
                or not 1 <= w["workspace"] <= 99:
            continue  # a tampered entry is skipped, never interpolated
        total += 1
        c = _client(w["address"])
        if c is None:
            missing += 1
            continue
        target = f"address:{w['address']}"
        ok = c["workspace"].get("id") == w["workspace"] or move_to_workspace(h, w["address"], w["workspace"])
        if bool((_client(w["address"]) or c).get("floating")) != bool(w.get("floating")):
            h.dispatch(dsp("window.float", action="set" if w.get("floating") else "unset", window=target))
        if w.get("floating") and (at := _pair(w.get("at"))) and (size := _pair(w.get("size"))):
            h.dispatch(dsp("window.resize", x=size[0], y=size[1], window=target))
            h.dispatch(dsp("window.move", x=at[0], y=at[1], window=target))
        restored += ok
    UNDO_FILE.unlink(missing_ok=True)
    return done(f"Repus {restored} janela(s)" + (f"; {missing} já tinham sido fechadas." if missing else "."),
                verified=restored + missing == total)


@tool("layouts.save", "layouts", "Guardar as janelas de uma área de trabalho como disposição.",
      {"name": _str(40, required=True, pattern=NAME.pattern), "workspace": _int(1, 99)},
      destructive=lambda args: (LAYOUTS_DIR / f"{args.get('name')}.json").exists())
def layouts_save(name: str, workspace: int | None = None) -> dict:
    h = hyprland()
    ws = workspace or h.query("activeworkspace").get("id")
    apps = list(applications().values())
    slots = []
    for c in h.clients():
        app = next((a for a in apps if _app_window(a)(c)), None) if c["workspace"].get("id") == ws else None
        if not app:
            continue
        slot: dict[str, Any] = {"app": app["id"]}
        if c.get("floating") and _pair(c.get("at")) and _pair(c.get("size")):
            mon = _monitor(h, c)
            s = mon.get("scale") or 1
            w, hgt = mon["width"] / s, mon["height"] / s
            x, y = (c["at"][0] - mon["x"]) * 100 / w, (c["at"][1] - mon["y"]) * 100 / hgt
            slot["rect"] = [max(0, min(90, round(x))), max(0, min(90, round(y))),
                            max(10, min(100, round(c["size"][0] * 100 / w))), max(10, min(100, round(c["size"][1] * 100 / hgt)))]
        slots.append(slot)
    if not slots:
        raise ActionError("not_found", f"Não há janelas de aplicações conhecidas na área {ws}.")
    LAYOUTS_DIR.mkdir(parents=True, exist_ok=True)
    path = LAYOUTS_DIR / f"{name}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"workspace": ws, "windows": slots[:8]}, indent=1))
    os.replace(tmp, path)
    return done(f"Guardei a disposição «{name}» com {len(slots[:8])} janelas.", verified=load_layout(name)["workspace"] == ws)


# --- Brave (through the PEACE extension) ------------------------------------------------------------------

def browser(op: str, args: dict, timeout: float = 5) -> Any:
    """One request to the native-messaging host, which relays it to the extension and back."""
    if not BROWSER_SOCKET or not BROWSER_SOCKET.exists():
        raise ActionError("browser_unavailable", "A extensão PEACE do Brave não está ligada (o Brave está aberto?).")
    request = {"id": uuid.uuid4().hex, "op": op, "args": args}
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(timeout)
            s.connect(str(BROWSER_SOCKET))
            s.sendall(json.dumps(request).encode() + b"\n")
            raw = s.makefile("rb").readline(1 << 20)
        reply = json.loads(raw)
    except (OSError, ValueError):
        raise ActionError("browser_unavailable", "O Brave não respondeu.") from None
    if not isinstance(reply, dict) or reply.get("id") != request["id"]:
        raise ActionError("browser_error", "Resposta inválida do Brave.")
    if not reply.get("ok"):
        raise ActionError("browser_error", str(reply.get("error") or "O Brave recusou a ação.")[:200])
    return reply.get("result")


def _tabs() -> list[dict]:
    tabs = browser("list", {})
    if not isinstance(tabs, list):
        raise ActionError("browser_error", "Resposta inválida do Brave.")
    return [t for t in tabs if isinstance(t, dict) and isinstance(t.get("id"), int) and not isinstance(t.get("id"), bool)]


def find_tabs(query: str, limit: int = 20) -> list[dict]:
    words = _fold(query).split()
    return [t for t in _tabs() if all(w in _fold(f"{t.get('title', '')} {t.get('url', '')}") for w in words)][:limit]


def _tab_item(t: dict) -> dict:
    return {"id": t["id"], "title": str(t.get("title", ""))[:120], "url": str(t.get("url", ""))[:200],
            "window": t.get("windowId"), "group": t.get("groupId", -1)}


def _matching(query: str) -> list[dict]:
    tabs = find_tabs(query)
    if not tabs:
        raise ActionError("not_found", f"Não encontrei separadores «{query}».")
    return tabs


QUERY = _str(100, required=True)
URL = _str(2000, required=True, pattern=r"https?://[^\s\x00-\x1f]+")
COLOR = _str(10, enum=list(GROUP_COLORS))


@tool("browser.tabs", "browser", "Listar ou procurar separadores do Brave pelo título ou endereço.", {"query": _str(100, minLength=0)})
def browser_tabs(query: str = "") -> dict:
    tabs = find_tabs(query, 100) if query.strip() else _tabs()[:100]
    return done(f"{len(tabs)} separador(es).", items=[_tab_item(t) for t in tabs])


@tool("browser.open", "browser", "Abrir um endereço web num separador novo (opcionalmente num grupo).",
      {"url": URL, "group": _str(40), "color": COLOR})
def browser_open(url: str, group: str | None = None, color: str | None = None) -> dict:
    tab = browser("open", {"url": url})
    if not isinstance(tab, dict) or not isinstance(tab.get("id"), int):
        raise ActionError("browser_error", "O Brave não abriu o separador.")
    if group:
        browser("group", {"tabIds": [tab["id"]], "title": group, **({"color": color} if color else {})})
    opened = next((t for t in _tabs() if t["id"] == tab["id"]), None)
    return done("Abri o separador" + (f" no grupo «{group}»." if group else "."), verified=opened is not None, tab=tab["id"])


@tool("browser.focus", "browser", "Mostrar um separador do Brave.", {"query": QUERY})
def browser_focus(query: str) -> dict:
    tab = _matching(query)[0]
    browser("focus", {"tabId": tab["id"]})
    active = next((t for t in _tabs() if t["id"] == tab["id"]), {})
    return done(f"Mostrei «{str(tab.get('title', ''))[:60]}».", verified=bool(active.get("active")))


@tool("browser.move", "browser", "Mover separadores para outra posição ou janela do Brave.",
      {"query": QUERY, "index": _int(-1, 500, default=-1), "window": _int(1, 2**31 - 1)})
def browser_move(query: str, index: int, window: int | None = None) -> dict:
    tabs = _matching(query)
    ids = {t["id"] for t in tabs}
    browser("move", {"tabIds": sorted(ids), "index": index, **({"windowId": window} if window is not None else {})})
    after = {t["id"]: t for t in _tabs() if t["id"] in ids}
    ok = len(after) == len(ids) and (window is None or all(t.get("windowId") == window for t in after.values()))
    return done(f"Movi {len(ids)} separador(es).", verified=ok)


@tool("browser.group", "browser", "Agrupar separadores do Brave sob um nome.", {"query": QUERY, "title": _str(40, required=True), "color": COLOR})
def browser_group(query: str, title: str, color: str | None = None) -> dict:
    tabs = _matching(query)
    group = browser("group", {"tabIds": [t["id"] for t in tabs], "title": title, **({"color": color} if color else {})})
    grouped = {t["id"] for t in _tabs() if t.get("groupId") == group}
    return done(f"Agrupei {len(tabs)} separador(es) em «{title}».", verified={t["id"] for t in tabs} <= grouped)


@tool("browser.ungroup", "browser", "Tirar separadores do seu grupo.", {"query": QUERY})
def browser_ungroup(query: str) -> dict:
    tabs = _matching(query)
    browser("ungroup", {"tabIds": [t["id"] for t in tabs]})
    ids = {t["id"] for t in tabs}
    return done(f"Desagrupei {len(tabs)} separador(es).", verified=all(t.get("groupId", -1) == -1 for t in _tabs() if t["id"] in ids))


@tool("browser.close", "browser", "Fechar separadores do Brave (recuperáveis no histórico).", {"query": QUERY}, destructive=True)
def browser_close(query: str) -> dict:
    tabs = _matching(query)
    ids = {t["id"] for t in tabs}
    browser("close", {"tabIds": sorted(ids)})
    left = ids & {t["id"] for t in _tabs()}
    return done(f"Fechei {len(ids) - len(left)} separador(es).", verified=not left,
                items=[{"title": str(t.get("title", ""))[:120]} for t in tabs])


# --- running ---------------------------------------------------------------------------------------------

def audit(entry: dict) -> None:
    """Which tool ran and how it ended; never arguments, paths, titles or URLs."""
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        if AUDIT_FILE.exists() and AUDIT_FILE.stat().st_size > AUDIT_MAX_BYTES:
            lines = AUDIT_FILE.read_text().splitlines()
            AUDIT_FILE.write_text("\n".join(lines[len(lines) // 2:]) + "\n")
        fd = os.open(AUDIT_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # auditing must never block an action that already happened


def run(name: Any, args: Any, confirmed: bool = False, job: str = "") -> dict:
    """Validate, check permission and confirmation, execute, and describe the outcome."""
    started = time.monotonic()
    result = {"tool": str(name)[:40], "ok": False, "verified": False, "code": None, "message": "", "data": {}}
    try:
        t = TOOLS.get(name) if isinstance(name, str) else None
        if t is None:
            raise ActionError("unknown_tool", "Ação desconhecida.")
        clean = validate(t.params, {} if args is None else args)
        if t.category not in ENABLED:
            raise ActionError("permission_denied", f"As ações de «{t.category}» estão desligadas neste PC.")
        if t.destructive(clean) and not confirmed:
            raise ActionError("confirmation_required", "Esta ação precisa da tua confirmação.")
        out = t.fn(**clean)
        result.update(ok=True, verified=bool(out["verified"]), message=out["message"], data=out.get("data") or {})
    except ActionError as e:
        result.update(code=e.code, message=e.message)
    except Exception as e:  # a tool bug must become a reported failure, not a dead agent
        result.update(code="internal", message="Erro interno ao executar a ação.", data={"error": type(e).__name__})
    audit({"at": round(time.time()), "job": str(job)[:40], "tool": result["tool"], "ok": result["ok"],
           "verified": result["verified"], "code": result["code"], "ms": round((time.monotonic() - started) * 1000)})
    return result


MAX_STEPS = 12


def run_plan(steps: Any, confirmed: bool, job: str = "", cancelled: Callable[[], bool] = lambda: False,
             progress: Callable[[list[dict]], None] = lambda results: None) -> tuple[list[dict], bool]:
    """Every step runs even after one fails (each result says what happened); cancellation stops between steps."""
    results: list[dict] = []
    for step in (steps if isinstance(steps, list) else [None])[:MAX_STEPS]:
        if cancelled():
            return results, True
        if isinstance(step, dict):
            results.append(run(step.get("tool"), step.get("args"), confirmed=confirmed, job=job))
        else:
            results.append(run(None, None, job=job))
        progress(results)
    return results, False


def availability() -> dict:
    """What the controller can reach right now, for the heartbeat."""
    try:
        hyprland()
        hypr = True
    except ActionError:
        hypr = False
    return {"categories": sorted(ENABLED), "hyprland": hypr, "browser": bool(BROWSER_SOCKET and BROWSER_SOCKET.exists())}

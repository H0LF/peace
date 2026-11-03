"""Where PEACE keeps the student's API keys: the operating system's keychain, or else an encrypted file.

Never in the database (it goes into backups), never in the settings, never sent to the dashboard.
The file exists because a server in Docker or under systemd often has no keychain. It is encrypted with a key derived
from PEACE_API_TOKEN, which lives elsewhere (the env file), so a copy of the data folder alone reveals nothing. Anyone
who can read both as this user can read the keys: that is the limit of any secret a background service must use.
Rotating the token makes the file unreadable: PEACE then reports "no key" and the student enters it again.
"""

import base64
import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from peace.config import get_settings
from peace.library import data_dir

log = logging.getLogger(__name__)
KEYCHAIN_TIMEOUT = 3.0  # a locked keychain can wait forever for a password prompt nobody sees


def _service() -> str:
    """One keychain entry per PEACE: development and the PEACE in use share this user's keychain."""
    return "peace-" + hashlib.sha256(b"peace-keychain:" + get_settings().api_token.encode()).hexdigest()[:8]


def _in_time(fn: Callable[[], Any]) -> tuple[bool, Any]:
    """(worked, result); a keychain that hangs or refuses counts as no keychain."""
    out: dict[str, Any] = {}

    def run() -> None:
        try:
            out["value"] = fn()
        except Exception as e:  # keyring backends raise their own types, dbus errors among them
            log.info("keychain unavailable: %s", type(e).__name__)  # the type only: never the message's contents

    t = threading.Thread(target=run, daemon=True, name="keychain")
    t.start()
    t.join(KEYCHAIN_TIMEOUT)
    return ("value" in out, out.get("value"))


def _keychain() -> Any | None:
    if os.environ.get("PEACE_SECRET_STORE") == "file":
        return None
    import keyring

    worked, backend = _in_time(keyring.get_keyring)
    if not worked or getattr(backend, "priority", 0) <= 0 or type(backend).__module__.endswith(("fail", "null")):
        return None
    return backend


def _path() -> Path:
    return data_dir() / "secrets.enc"


def _fernet() -> Fernet:
    digest = hashlib.sha256(b"peace-secret-file-v1:" + get_settings().api_token.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _read_file() -> dict[str, str]:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_file(data: dict[str, str]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(temp, path)


def get(name: str) -> str | None:
    if chain := _keychain():
        worked, value = _in_time(lambda: chain.get_password(_service(), name))
        if worked and value:
            return value
    token = _read_file().get(name)
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:  # the API token was rotated
        return None


def put(name: str, value: str) -> str:
    """Stores the secret and says where: "keychain" or "file". Only one place holds it."""
    if chain := _keychain():
        worked, _ = _in_time(lambda: chain.set_password(_service(), name, value))
        if worked:
            _drop_from_file(name)
            return "keychain"
    _write_file(_read_file() | {name: _fernet().encrypt(value.encode()).decode()})
    return "file"


def _drop_from_file(name: str) -> None:
    data = _read_file()
    if name in data:
        del data[name]
        _write_file(data)


def delete(name: str) -> None:
    if chain := _keychain():
        _in_time(lambda: chain.delete_password(_service(), name))  # raises when absent: nothing to remove
    _drop_from_file(name)


def where() -> str:
    return "keychain" if _keychain() else "file"

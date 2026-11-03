"""Updates: only signed, stable, newer releases; and a host script that can always go back."""

import base64
import io
import json
import os
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from peace import release, routes, updates
from peace.api import app

REPO_ROOT = Path(__file__).parents[1]
GOOD = f"https://github.com/{updates.REPO}/releases/download/v9.0.0/"


@pytest.fixture
def keys(tmp_path):
    key = Ed25519PrivateKey.generate()
    file = tmp_path / "signing.key"
    file.write_text(base64.b64encode(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode())
    return file, base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def bundle(path: Path, version="9.0.0", extra: dict[str, bytes] | None = None) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in ({"pyproject.toml": f'version = "{version}"\n'.encode(), "scripts/update.sh": b"#!/bin/sh\n"} | (extra or {})).items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def signed(tmp_path, keys, version="9.0.0"):
    b = bundle(tmp_path / "b.tar.gz", version)
    s = tmp_path / "b.sig"
    s.write_bytes(release.sign(b, version, keys[0]))
    return b, s


# --- verifying a release ------------------------------------------------------------------------------------------


def test_a_signed_bundle_unpacks(tmp_path, keys):
    b, s = signed(tmp_path, keys)
    dest = tmp_path / "out"
    dest.mkdir()
    release.unpack(b, s, "9.0.0", dest, keys[1])
    assert (dest / "scripts/update.sh").is_file()


def test_a_changed_bundle_is_refused_before_anything_is_written(tmp_path, keys):
    b, s = signed(tmp_path, keys)
    b.write_bytes(b.read_bytes() + b"x")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(release.BadRelease):
        release.unpack(b, s, "9.0.0", dest, keys[1])
    assert not list(dest.iterdir())


def test_a_signature_from_another_key_is_refused(tmp_path, keys):
    b, s = signed(tmp_path, keys)
    other = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    with pytest.raises(release.BadRelease):
        release.verify(b, s, "9.0.0", other)


def test_an_old_signed_release_cannot_pretend_to_be_new(tmp_path, keys):
    b, s = signed(tmp_path, keys, "1.0.0")
    with pytest.raises(release.BadRelease):
        release.verify(b, s, "9.0.0", keys[1])


def test_garbage_signature_and_bad_version_are_refused_not_crashes(tmp_path, keys):
    b, s = signed(tmp_path, keys)
    s.write_text("not base64 !!!")
    with pytest.raises(release.BadRelease):
        release.verify(b, s, "9.0.0", keys[1])
    with pytest.raises(release.BadRelease):
        release.verify(b, s, "../9", keys[1])


def test_a_signed_bundle_with_paths_leaving_the_folder_is_refused(tmp_path, keys):
    b = bundle(tmp_path / "evil.tar.gz", extra={"../../evil": b"x"})
    s = tmp_path / "evil.sig"
    s.write_bytes(release.sign(b, "9.0.0", keys[0]))
    dest = tmp_path / "a" / "b"
    dest.mkdir(parents=True)
    with pytest.raises(tarfile.TarError):
        release.unpack(b, s, "9.0.0", dest, keys[1])
    assert not (tmp_path / "evil").exists()


def test_a_bundle_of_another_version_is_refused(tmp_path, keys):
    b = bundle(tmp_path / "b.tar.gz", version="8.0.0")
    s = tmp_path / "s"
    s.write_bytes(release.sign(b, "9.0.0", keys[0]))
    dest = tmp_path / "o"
    dest.mkdir()
    with pytest.raises(release.BadRelease):
        release.unpack(b, s, "9.0.0", dest, keys[1])


def test_keygen_never_replaces_a_key(tmp_path):
    f = tmp_path / "k"
    assert release.keygen(f)
    assert oct(f.stat().st_mode)[-3:] == "600"
    with pytest.raises(SystemExit):
        release.keygen(f)


def test_the_real_public_key_ships_in_the_code():
    assert "@@" not in release.PUBLIC_KEY and len(base64.b64decode(release.PUBLIC_KEY)) == 32


# --- what is offered ----------------------------------------------------------------------------------------------


def github(monkeypatch, status=200, **fields):
    data = {"tag_name": "v9.0.0", "body": "Novidades", "draft": False, "prerelease": False,
            "assets": [{"name": "peace-9.0.0.tar.gz", "browser_download_url": GOOD + "peace-9.0.0.tar.gz"},
                       {"name": "peace-9.0.0.tar.gz.sig", "browser_download_url": GOOD + "peace-9.0.0.tar.gz.sig"}]} | fields
    monkeypatch.setattr(httpx, "get", lambda url, **k: httpx.Response(status, json=data, request=httpx.Request("GET", url)))


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setenv("PEACE_INSTALL_KIND", "docker")
    monkeypatch.setattr(updates, "current_version", lambda: "1.0.0")


def test_a_new_release_is_shown_with_its_notes_then_installs_when_asked(client, monkeypatch, managed):
    github(monkeypatch)
    r = client.post("/api/v1/updates/check").json()
    assert r["version"] == "1.0.0" and r["available"]["version"] == "9.0.0" and r["available"]["notes"] == "Novidades"
    assert client.get("/api/v1/updates/pending").json() == {"install": False}  # default policy: the student decides
    assert client.post("/api/v1/updates/install").json()["status"] == "requested"
    pending = client.get("/api/v1/updates/pending").json()
    assert pending["install"] is True and pending["version"] == "9.0.0" and pending["bundle"].endswith("peace-9.0.0.tar.gz")


@pytest.mark.parametrize("fields", [{"prerelease": True}, {"draft": True}, {"tag_name": "nightly"}, {"assets": []},
                                    {"assets": [{"name": "peace-9.0.0.tar.gz", "browser_download_url": GOOD + "x"}]},
                                    {"assets": [{"name": "peace-9.0.0.tar.gz", "browser_download_url": "https://evil.example/x"},
                                                {"name": "peace-9.0.0.tar.gz.sig", "browser_download_url": "https://evil.example/y"}]}])
def test_work_in_progress_unsigned_or_foreign_downloads_are_never_offered(client, monkeypatch, managed, fields):
    github(monkeypatch, **fields)
    assert client.post("/api/v1/updates/check").json()["available"] is None


def test_the_same_or_an_older_version_is_not_offered(client, monkeypatch, managed):
    github(monkeypatch)
    monkeypatch.setattr(updates, "current_version", lambda: "9.0.0")
    assert client.post("/api/v1/updates/check").json()["available"] is None
    monkeypatch.setattr(updates, "current_version", lambda: "10.0.0")
    assert client.get("/api/v1/updates").json()["available"] is None
    assert client.post("/api/v1/updates/install").status_code == 409


def test_versions_compare_as_numbers(monkeypatch, managed):
    monkeypatch.setattr(updates, "current_version", lambda: "1.9.0")
    assert updates.newer({"latest": {"version": "1.10.0"}})


def test_automatic_policy_installs_but_a_failed_version_waits_for_the_student(client, monkeypatch, managed):
    github(monkeypatch)
    client.post("/api/v1/updates/check")
    assert client.put("/api/v1/updates/policy", json={"policy": "auto"}).json()["policy"] == "auto"
    assert client.get("/api/v1/updates/pending").json()["install"] is True
    assert client.post("/api/v1/updates/report", json={"status": "failed", "message": "start"}).status_code == 204
    assert client.get("/api/v1/updates/pending").json()["install"] is False  # a bad release cannot loop
    view = client.get("/api/v1/updates").json()
    assert view["status"] == "failed" and view["message"] == "start"
    client.post("/api/v1/updates/install")
    assert client.get("/api/v1/updates/pending").json()["install"] is True


def test_a_failed_check_keeps_what_was_known(client, monkeypatch, managed):
    github(monkeypatch)
    client.post("/api/v1/updates/check")

    def down(*a, **k):
        raise httpx.ConnectError("no")
    monkeypatch.setattr(httpx, "get", down)
    r = client.post("/api/v1/updates/check").json()
    assert r["check_error"] == "network" and r["available"]["version"] == "9.0.0"


def test_a_copy_managed_elsewhere_only_shows_its_version(client, monkeypatch, session):
    monkeypatch.delenv("PEACE_INSTALL_KIND", raising=False)
    assert client.get("/api/v1/updates").json()["kind"] is None
    assert client.post("/api/v1/updates/check").status_code == 409
    assert client.post("/api/v1/updates/install").status_code == 409
    assert client.get("/api/v1/updates/pending").json() == {"install": False}
    assert updates.daily_check(session, datetime.now(timezone.utc)) is False


def test_the_worker_checks_once_a_day_and_never_when_off(client, monkeypatch, managed, session):
    calls = []
    monkeypatch.setattr(updates, "latest_release", lambda: calls.append(1))
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert updates.daily_check(session, now) is True
    assert updates.daily_check(session, now + timedelta(hours=23)) is False
    assert updates.daily_check(session, now + timedelta(hours=25)) is True
    client.put("/api/v1/updates/policy", json={"policy": "off"})
    session.expire_all()
    assert updates.daily_check(session, now + timedelta(days=9)) is False and len(calls) == 2


def test_an_install_that_never_reports_back_shows_as_failed(client, monkeypatch, managed):
    github(monkeypatch)
    client.post("/api/v1/updates/check")
    client.post("/api/v1/updates/install")
    later = routes.get_now() + timedelta(hours=2)
    app.dependency_overrides[routes.get_now] = lambda: later
    try:
        assert client.get("/api/v1/updates").json()["status"] == "failed"
    finally:
        app.dependency_overrides.pop(routes.get_now)


# --- the script on the host ---------------------------------------------------------------------------------------


class Host:
    """A temporary install, with every outside program replaced by a small script that writes down what it was asked."""

    def __init__(self, tmp: Path, keys, kind: str):
        self.tmp, self.kind = tmp, kind
        self.app, self.bin, self.log, self.www = tmp / "top/app", tmp / "bin", tmp / "calls.log", tmp / "www"
        for d in (self.app / "scripts", self.bin, self.www):
            d.mkdir(parents=True)
        (self.app / "scripts/update.sh").write_bytes((REPO_ROOT / "scripts/update.sh").read_bytes())
        (self.app / "compose.yaml").write_text("old\n")
        (self.app / "OLD").write_text("old code\n")
        self.envfile = tmp / "peace.env"
        self.envfile.write_text("PEACE_API_TOKEN=tok\nPEACE_DATABASE_URL=postgresql+psycopg://peace@x/peace\n")
        self.private, self.public = keys
        if kind == "docker":
            (self.app / ".env").write_text(f"PEACE_ENV_FILE={self.envfile}\n")
        else:
            # Verifies with the test key, through the real code.
            venv = self.app / ".venv/bin"
            venv.mkdir(parents=True)
            (venv / "python").write_text(
                f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(REPO_ROOT / 'src')!r})\nfrom peace import release\n"
                f"release.PUBLIC_KEY = {self.public!r}\nraise SystemExit(release.main(sys.argv[3:]))\n")
            (venv / "python").chmod(0o755)
        for name in ("uv", "systemctl", "pg_restore"):
            self.tool(name, f'echo "{name} $*" >> {self.log}')
        self.tool("pg_dump", f'echo "pg_dump $*" >> {self.log}; echo dump')
        (self.www / "pending").write_text('{"install":false}')
        # curl: the API from files, /health by whichever code is in place.
        self.tool("curl", f'''
out=""; url=""; body=""
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; -d) body=$2; shift 2;; -H|-X|--max-time) shift 2;; -*) shift;; *) url=$1; shift;; esac; done
echo "curl $url $body" >> {self.log}
case "$url" in
  */updates/pending) cat {self.www}/pending;;
  */updates/report) ;;
  */health) [ -f "{self.app}/HEALTHY" ];;
  *) cp "{self.www}/$(basename "$url")" "$out";;
esac
''')

    def tool(self, name, body):
        f = self.bin / name
        f.write_text("#!/usr/bin/env bash\n" + body)
        f.chmod(0o755)

    def publish(self, version="9.0.0", healthy=True, sign_with=None, tamper=False):
        extra = {"HEALTHY": b""} if healthy else {}
        b = bundle(self.www / f"peace-{version}.tar.gz", version, extra | {"NEW": b"new code\n"})
        (self.www / f"peace-{version}.tar.gz.sig").write_bytes(release.sign(b, version, sign_with or self.private))
        if tamper:
            b.write_bytes(b.read_bytes() + b"x")
        (self.www / "pending").write_text(json.dumps({"install": True, "version": version, "bundle": f"http://x/peace-{version}.tar.gz",
                                                      "signature": f"http://x/peace-{version}.tar.gz.sig"}, separators=(",", ":")))

    def run(self):
        env = os.environ | {"PATH": f"{self.bin}:/usr/bin:/bin", "PEACE_URL": "http://x", "HEALTH_TRIES": "2", "HEALTH_WAIT": "0",
                            "PEACE_ENV_FILE": str(self.envfile), "HOME": str(self.tmp), "PEACE_KIND": self.kind}
        return subprocess.run(["bash", str(self.app / "scripts/update.sh")], env=env, capture_output=True, text=True, timeout=60)

    @property
    def calls(self):
        return self.log.read_text() if self.log.exists() else ""


@pytest.fixture
def host(tmp_path, keys):
    return Host(tmp_path, keys, "linux")


def test_nothing_due_means_nothing_happens(host):
    assert host.run().returncode == 0
    assert (host.app / "OLD").exists() and "pg_dump" not in host.calls


def test_a_good_update_backs_up_first_swaps_and_reports(host):
    host.publish()
    r = host.run()
    assert r.returncode == 0, r.stderr
    assert (host.app / "NEW").exists() and not (host.app / "OLD").exists()
    assert (host.app.parent / "app.previous/OLD").exists()
    calls = host.calls
    assert calls.index("pg_dump") < calls.index("uv sync") < calls.index("systemctl --user restart peace-api peace-worker")
    assert '"status":"installing"' in calls and '"status":"done"' in calls
    assert "postgresql://peace@x/peace" in calls and "+psycopg" not in calls  # pg_dump does not know SQLAlchemy's dialect


def test_a_tampered_download_changes_nothing(host):
    host.publish(tamper=True)
    r = host.run()
    assert r.returncode == 1 and "signature" in r.stderr
    assert (host.app / "OLD").exists() and not (host.app / "NEW").exists()
    assert "pg_dump" not in host.calls and "restart" not in host.calls
    assert '"status":"failed","message":"signature"' in host.calls


def test_a_release_signed_by_someone_else_changes_nothing(host, tmp_path):
    other = tmp_path / "other.key"
    release.keygen(other)
    host.publish(sign_with=other)
    assert host.run().returncode == 1
    assert (host.app / "OLD").exists()


def test_a_failed_backup_stops_before_touching_the_code(host):
    host.tool("pg_dump", "exit 1")
    host.publish()
    assert host.run().returncode == 1
    assert (host.app / "OLD").exists() and "restart" not in host.calls


def test_a_version_that_does_not_start_goes_back_to_the_old_code_and_database(host):
    (host.app / "HEALTHY").write_text("")
    host.publish(healthy=False)
    r = host.run()
    assert r.returncode == 1 and "going back" in r.stderr
    assert (host.app / "OLD").exists() and not (host.app / "NEW").exists()
    assert (host.app.parent / "app.failed/NEW").exists()  # kept to look at
    assert "pg_restore -d postgresql://peace@x/peace --clean --if-exists" in host.calls
    assert '"status":"failed","message":"start"' in host.calls


def test_when_going_back_also_fails_it_says_where_the_backup_is(host):
    host.publish(healthy=False)  # no HEALTHY in the old code either
    r = host.run()
    assert r.returncode == 2 and "before-9.0.0.dump" in r.stderr
    assert (host.app.parent / ".peace-update/before-9.0.0.dump").read_text().strip() == "dump"


def test_docker_install_keeps_its_settings_file_and_uses_compose(tmp_path, keys):
    host = Host(tmp_path, keys, "docker")
    # The container's verification, played by the real code on this machine.
    host.tool("docker", f'''
echo "docker $*" >> {host.log}
case "$*" in
  *"run --rm"*) w=$(printf '%s\\n' "$@" | sed -n 's#:/update$##p'); PYTHONPATH={REPO_ROOT / "src"} {sys.executable} -c "
from peace import release; from pathlib import Path; w=Path('$w')
release.unpack(w/'peace.tar.gz', w/'peace.sig', '9.0.0', w/'new', {host.public!r})" ;;
  *pg_dump*) echo dump ;;
esac
''')
    host.publish()
    r = host.run()
    assert r.returncode == 0, r.stderr
    assert (host.app / "NEW").exists() and str(host.envfile) in (host.app / ".env").read_text()
    calls = host.calls
    assert calls.index("exec -T db pg_dump") < calls.index("compose up -d --build")


def test_the_public_scripts_exist_and_parse():
    for script in ("scripts/update.sh", "install-mac.sh", "scripts/uninstall-mac.sh", "install.sh"):
        assert subprocess.run(["bash", "-n", str(REPO_ROOT / script)]).returncode == 0
    assert (REPO_ROOT / "scripts/update.ps1").is_file()


@pytest.mark.parametrize("ram,free,best", [(8, 500, "4"), (16, 500, "2"), (32, 500, "1"), (64, 25, "2"), (4, 500, "5"), (32, 17, "4")])
def test_the_mac_installer_recommends_by_memory_and_disk(ram, free, best):
    r = subprocess.run(["bash", "-c", f"source {REPO_ROOT}/install-mac.sh; recommended {ram} {free}"],
                       env=os.environ | {"PEACE_INSTALLER_TEST": "1"}, capture_output=True, text=True)
    assert r.stdout.strip() == best, r.stderr

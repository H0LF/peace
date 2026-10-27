"""scripts/backup.sh and scripts/restore.sh against the throwaway *_test database (direct mode)."""
import os
import shutil
import stat
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from peace.config import database_url
from peace.models import Task

SCRIPTS = Path(__file__).parents[1] / "scripts"
pytestmark = pytest.mark.skipif(not shutil.which("pg_dump"), reason="PostgreSQL client tools are not on PATH")
MARKER = "not-a-real-password-marker"


def env(tmp_path, **extra):
    url = database_url()
    return os.environ | {
        "PEACE_BACKUP_MODE": "direct", "PEACE_BACKUP_DIR": str(tmp_path / "backups"),
        "PGHOST": url.host, "PGPORT": str(url.port), "PGUSER": url.username, "PGDATABASE": url.database,
        "PGPASSWORD": MARKER,  # ignored by the trust-auth test cluster; must never be printed
    } | extra


def run(script, *args, **kw):
    out = subprocess.run([SCRIPTS / script, *args], env=env(**kw), capture_output=True, text=True, timeout=120)
    assert MARKER not in out.stdout + out.stderr
    return out


def dumps(tmp_path):
    return sorted(p.name for p in (tmp_path / "backups").glob("peace-*.dump"))


@pytest.fixture
def scratch_db():
    name = f"peace_restore_{uuid.uuid4().hex[:8]}_test"
    yield name
    admin = create_engine(database_url().set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    admin.dispose()


def count_tasks(db):
    engine = create_engine(database_url().set(database=db))
    with engine.connect() as conn:
        n = conn.scalar(text("SELECT count(*) FROM tasks"))
    engine.dispose()
    return n


def test_backup_restores_into_a_new_database(session, tmp_path, scratch_db):
    session.add_all([Task(title="Relatório"), Task(title="Ficha")])
    session.commit()
    out = run("backup.sh", "db", tmp_path=tmp_path)
    assert out.returncode == 0, out.stderr
    (name,) = dumps(tmp_path)
    backups = tmp_path / "backups"
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700
    assert stat.S_IMODE((backups / name).stat().st_mode) == 0o600
    assert not list(backups.glob(".*.partial"))
    out = run("restore.sh", str(backups / name), scratch_db, tmp_path=tmp_path)
    assert out.returncode == 0, out.stderr
    assert count_tasks(scratch_db) == 2


def test_restore_picks_a_valid_new_database_name_by_default(tmp_path):
    assert run("backup.sh", "db", tmp_path=tmp_path).returncode == 0
    out = run("restore.sh", str(tmp_path / "backups" / dumps(tmp_path)[0]), tmp_path=tmp_path)
    assert out.returncode == 0, out.stderr
    name = out.stderr.split("ok: ")[1].split()[0]
    admin = create_engine(database_url().set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE "{name}"'))
    admin.dispose()
    assert name.startswith("peace_restore_")


def test_restore_never_overwrites_an_existing_database(session, tmp_path):
    session.add(Task(title="Original"))
    session.commit()
    assert run("backup.sh", "db", tmp_path=tmp_path).returncode == 0
    session.add(Task(title="Depois do backup"))
    session.commit()
    out = run("restore.sh", str(tmp_path / "backups" / dumps(tmp_path)[0]), database_url().database, tmp_path=tmp_path)
    assert out.returncode != 0 and "nothing was changed" in out.stderr
    assert count_tasks(database_url().database) == 2


def test_restore_refuses_a_corrupted_backup(session, tmp_path, scratch_db):
    assert run("backup.sh", "db", tmp_path=tmp_path).returncode == 0
    dump = tmp_path / "backups" / dumps(tmp_path)[0]
    dump.write_bytes(dump.read_bytes()[: dump.stat().st_size // 2])  # truncated, e.g. a half-copied file
    out = run("restore.sh", str(dump), scratch_db, tmp_path=tmp_path)
    assert out.returncode != 0 and "checksum mismatch" in out.stderr
    admin = create_engine(database_url().set(database="postgres"))
    with admin.connect() as conn:
        assert not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": scratch_db})
    admin.dispose()


def test_failed_backup_leaves_nothing_and_keeps_older_backups(tmp_path):
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "peace-20200101T000000Z.dump").write_text("older backup")
    before = dumps(tmp_path)
    out = run("backup.sh", "db", tmp_path=tmp_path, PGDATABASE="no_such_database_test")
    assert out.returncode != 0 and "FAILED: pg_dump" in out.stderr
    assert dumps(tmp_path) == before
    assert not list((tmp_path / "backups").glob(".*.partial"))


def test_retention_removes_only_the_oldest_backups_of_the_same_kind(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    for f in ["peace-20200101T000000Z.dump", "peace-20200102T000000Z.dump", "vault-20200101T000000Z.tar.gz", "notes.txt"]:
        (backups / f).write_text("x")
        (backups / f"{f}.sha256").write_text("x")
    assert run("backup.sh", "db", tmp_path=tmp_path, PEACE_BACKUP_KEEP="2").returncode == 0
    names = {p.name for p in backups.iterdir()}
    assert "peace-20200101T000000Z.dump" not in names and "peace-20200101T000000Z.dump.sha256" not in names
    assert {"peace-20200102T000000Z.dump", "vault-20200101T000000Z.tar.gz", "notes.txt"} <= names
    assert len(dumps(tmp_path)) == 2


def test_vault_backup_reads_but_never_changes_the_vault(tmp_path):
    vault = tmp_path / "My Vault"
    (vault / "01 - Daily Notes").mkdir(parents=True)
    note = vault / "01 - Daily Notes" / "hoje.md"
    note.write_text("# Notas minhas\n")
    before = note.stat().st_mtime_ns
    out = run("backup.sh", "vault", tmp_path=tmp_path, PEACE_VAULT=str(vault))
    assert out.returncode == 0, out.stderr
    (archive,) = (tmp_path / "backups").glob("vault-*.tar.gz")
    listing = subprocess.run(["tar", "-tzf", archive], capture_output=True, text=True, check=True).stdout
    assert "My Vault/01 - Daily Notes/hoje.md" in listing
    assert note.read_text() == "# Notas minhas\n" and note.stat().st_mtime_ns == before


def test_backups_are_copied_off_the_disk_and_verified_there(tmp_path):
    copy = tmp_path / "external drive" / "peace"
    out = run("backup.sh", "db", tmp_path=tmp_path, PEACE_BACKUP_COPY_TO=str(copy))
    assert out.returncode == 0, out.stderr
    [name] = dumps(tmp_path)
    assert (copy / name).read_bytes() == (tmp_path / "backups" / name).read_bytes()
    assert subprocess.run(["sha256sum", "-c", f"{name}.sha256"], cwd=copy, capture_output=True).returncode == 0
    assert "copied and verified" in out.stderr and not list(copy.glob(".*partial"))


def test_a_corrupted_copy_is_refused_and_the_local_backup_kept(tmp_path):
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "cp").write_text('#!/bin/sh\n/usr/bin/cp "$@" && for last; do :; done && echo corrupt >> "$last"\n')
    (shim / "cp").chmod(0o755)
    copy = tmp_path / "copy"
    out = run("backup.sh", "db", tmp_path=tmp_path, PEACE_BACKUP_COPY_TO=str(copy), PATH=f"{shim}:{os.environ['PATH']}")
    assert out.returncode == 1 and "does not match" in out.stderr
    assert len(dumps(tmp_path)) == 1 and list(copy.iterdir()) == []


def test_a_backup_without_a_second_place_says_so(tmp_path):
    out = run("backup.sh", "db", tmp_path=tmp_path)
    assert out.returncode == 0 and "on this disk only" in out.stderr


def test_backups_are_copied_over_ssh_and_verified_on_the_other_host(tmp_path):
    shim = tmp_path / "bin"
    shim.mkdir()
    # Stand-in for ssh: runs the remote command locally, as the remote shell would.
    (shim / "ssh").write_text('#!/bin/sh\nwhile [ "$1" != "--" ]; do shift; done\nshift 2\nexec sh -c "$1"\n')
    (shim / "ssh").chmod(0o755)
    copy = tmp_path / "server copies" / "peace"
    out = run("backup.sh", "db", tmp_path=tmp_path, PEACE_BACKUP_COPY_TO=f"backuphost:{copy}", PATH=f"{shim}:{os.environ['PATH']}")
    assert out.returncode == 0, out.stderr
    [name] = dumps(tmp_path)
    assert (copy / name).read_bytes() == (tmp_path / "backups" / name).read_bytes()
    assert subprocess.run(["sha256sum", "-c", f"{name}.sha256"], cwd=copy, capture_output=True).returncode == 0
    assert oct(copy.stat().st_mode & 0o777) == "0o700"

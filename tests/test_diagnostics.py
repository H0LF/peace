"""/api/v1/diagnostics and the backup alert: built from existing state, never a secret."""
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from peace import diagnostics, llm
from peace.api import app
from peace.models import Notification
from peace.routes import get_now

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    app.dependency_overrides[get_now] = lambda: NOW
    monkeypatch.setattr(diagnostics, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(diagnostics, "_backup_cache", {"at": -1e9, "value": None})
    monkeypatch.setattr(llm, "llm_reachable", lambda: False)
    monkeypatch.setattr(llm, "loaded_models", lambda: [])
    monkeypatch.setattr(llm, "gpu_memory", lambda: None)
    from peace import assistant
    monkeypatch.setattr(assistant, "llm_reachable", lambda: False)
    yield tmp_path
    app.dependency_overrides.pop(get_now, None)


def backup(dir_, kind, name, hours_ago, **status):
    f = dir_ / name
    f.write_text("x")
    t = (NOW - timedelta(hours=hours_ago)).timestamp()
    os.utime(f, (t, t))
    if status:
        (dir_ / f"status-{kind}.json").write_text(json.dumps({"ok": True} | status))


def by_id(client):
    return {c["id"]: c for c in client.get("/api/v1/diagnostics").json()["components"]}


def test_every_component_is_reported_with_a_stable_code(client, setup):
    backup(setup, "db", "peace-20261027T033000Z.dump", 8, offsite="verified", encrypted=False, restore_test="passed")
    backup(setup, "vault", "vault-20261020T033000Z.tar.gz", 7 * 24)
    c = by_id(client)
    assert c["database"]["state"] == "ok" and c["worker"]["state"] == "off"
    assert (c["backup_db"]["state"], c["backup_db"]["code"]) == ("warn", "backup_unencrypted")
    assert "reposição testada" in c["backup_db"]["detail"]
    assert (c["backup_vault"]["state"], c["backup_vault"]["code"]) == ("error", "backup_stale")
    assert (c["backup_repo"]["code"], c["model"]["code"], c["desktop"]["code"]) == ("backup_missing", "model_unavailable", "agent_offline")


def test_a_failed_or_late_database_backup_raises_one_alert_a_day(client, session, setup):
    backup(setup, "db", "peace-20261027T033000Z.dump", 8, ok=False, error="pg_dump exited with an error")
    client.get("/api/v1/notifications/pending")
    client.get("/api/v1/notifications/pending")
    [alert] = session.query(Notification).filter(Notification.key.like("backup:%")).all()
    assert "pg_dump" in alert.body and alert.kind == "integration"


def test_no_backup_folder_means_no_alert(client, session):
    client.get("/api/v1/notifications/pending")
    assert session.query(Notification).count() == 0


def test_the_report_never_carries_the_token(client):
    from peace.config import get_settings
    assert get_settings().api_token not in client.get("/api/v1/diagnostics").text

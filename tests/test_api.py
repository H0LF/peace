import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from peace.api import app
from peace.db import get_session
from peace.models import AuditEvent


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


def test_health_reports_database_down():
    dead = create_engine("postgresql+psycopg://peace@127.0.0.1:1/none", connect_args={"connect_timeout": 1})

    def broken_session():
        with Session(dead) as s:
            yield s

    app.dependency_overrides[get_session] = broken_session
    try:
        r = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    assert r.json()["database"] == "down"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}])
def test_api_rejects_missing_or_wrong_token(headers):
    assert TestClient(app).get("/api/v1/audit", headers=headers).status_code == 401


def test_audit_lists_newest_first(client, session):
    session.add_all([AuditEvent(actor="test", action="first"), AuditEvent(actor="test", action="second")])
    session.commit()
    body = client.get("/api/v1/audit").json()
    assert [e["action"] for e in body] == ["second", "first"]

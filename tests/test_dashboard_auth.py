import pytest
from fastapi.testclient import TestClient

from conftest import TOKEN
from peace import api


def test_login_sets_strict_httponly_cookie_that_authorizes_api():
    c = TestClient(api.app)
    assert c.post("/auth/login", json={"token": "wrong"}).status_code == 401
    assert c.get("/auth/session").status_code == 401
    r = c.post("/auth/login", json={"token": TOKEN})
    assert r.status_code == 204
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and TOKEN.lower() not in cookie
    assert c.get("/auth/session").status_code == 204
    assert c.get("/api/v1/courses").status_code == 200
    c.post("/auth/logout")
    assert c.get("/api/v1/courses").status_code == 401


def test_forged_cookie_is_rejected():
    c = TestClient(api.app, cookies={"peace_session": "0" * 64})
    assert c.get("/api/v1/courses").status_code == 401


@pytest.fixture
def built(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("js")
    (tmp_path.parent / "secret.txt").write_text("nope")
    monkeypatch.setattr(api, "DASHBOARD", tmp_path.resolve())
    return TestClient(api.app)


def test_spa_serves_files_falls_back_to_index_and_blocks_traversal(built):
    assert built.get("/assets/app.js").text == "js"
    assert built.get("/tarefas").text == "<html>app</html>"  # client-side route
    assert built.get("/../secret.txt").text == "<html>app</html>"
    assert built.get("/assets/..%2F..%2Fsecret.txt").text == "<html>app</html>"
    assert built.get("/api/v1/nope").status_code in (401, 404)  # never the SPA for API paths

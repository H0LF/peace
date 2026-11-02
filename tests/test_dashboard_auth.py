import pytest
from fastapi.testclient import TestClient

from conftest import TOKEN
from peace import api, auth


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
    c = TestClient(api.app, cookies={auth.session_cookie(): "0" * 64})
    assert c.get("/api/v1/courses").status_code == 401


def test_each_peace_has_its_own_cookie(monkeypatch):
    """Two PEACEs on one host (stable 8420, development 8421) must not log each other out."""
    mine = auth.session_cookie()
    other = auth.get_settings().__class__(**{**vars(auth.get_settings()), "api_token": "another-peace-token-of-32-characters"})
    monkeypatch.setattr(auth, "get_settings", lambda: other)
    assert auth.session_cookie() != mine and auth.session_cookie().startswith("peace_session_")


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


def test_a_cookie_from_another_local_page_cannot_write(client):
    from fastapi.testclient import TestClient

    from peace.api import app
    from peace.auth import session_cookie, session_value
    from peace.config import get_settings

    browser = TestClient(app, cookies={session_cookie(): session_value(get_settings().api_token)})
    assert browser.post("/api/v1/sync", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert browser.post("/api/v1/sync", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert browser.get("/api/v1/tasks", headers={"Sec-Fetch-Site": "same-site"}).status_code == 200
    assert browser.post("/api/v1/plan/day", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_changing_a_sites_address_needs_its_password_again(client):
    body = {"moodle": {"enabled": True, "url": "https://moodle.example.pt/moodle", "username": "a", "password": "segredo"},
            "inforestudante": {"enabled": False, "url": "", "username": "", "password": ""}}
    assert client.put("/api/v1/preferences/integrations", json=body).status_code == 200
    body["moodle"] |= {"password": "", "username": "b"}
    assert client.put("/api/v1/preferences/integrations", json=body).status_code == 200  # same address: password kept
    body["moodle"]["url"] = "https://evil.example.com"
    r = client.put("/api/v1/preferences/integrations", json=body)
    assert r.status_code == 422 and "palavra-passe" in r.json()["detail"]

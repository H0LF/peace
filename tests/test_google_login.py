"""Sign in with Google: PKCE + state + nonce, the first account becomes the owner, others are refused."""

import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from conftest import TOKEN
from peace import api, auth, google

CLIENT = "123-test.apps.googleusercontent.com"


@pytest.fixture
def browser(monkeypatch):
    """A browser that opened the installer's link once (token session), as every fresh install does."""
    monkeypatch.setenv("PEACE_GOOGLE_CLIENT_ID", CLIENT)
    monkeypatch.setenv("PEACE_GOOGLE_CLIENT_SECRET", "not-secret-for-desktop-apps")
    c = TestClient(api.app, base_url="http://127.0.0.1:8421", follow_redirects=False)
    assert c.post("/auth/login", json={"token": TOKEN}).status_code == 204
    return c


def stranger():
    return TestClient(api.app, base_url="http://127.0.0.1:8421", follow_redirects=False)


def id_token(**claims) -> str:
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"e30.{part}.sig"


def start(browser) -> dict:
    r = browser.get("/auth/google/start")
    assert r.status_code == 303
    url = urlparse(r.headers["location"])
    assert url.netloc == "accounts.google.com"
    return {k: v[0] for k, v in parse_qs(url.query).items()}


def sign_in(browser, monkeypatch, sub="111", email="ana@gmail.com", **overrides):
    q = start(browser)
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT, "sub": sub, "email": email, "email_verified": True,
              "exp": time.time() + 600, "nonce": q["nonce"], **overrides}
    seen = {}

    def exchange(code, verifier, redirect_uri):
        seen.update(code=code, verifier=verifier, redirect_uri=redirect_uri)
        return {"id_token": id_token(**claims)}
    monkeypatch.setattr(google, "exchange", exchange)
    r = browser.get("/auth/google/callback", params={"code": "c0de", "state": q["state"]})
    return q, seen, r


def test_status_says_whether_google_is_available(browser, monkeypatch):
    assert browser.get("/auth/google").json() == {"available": True}
    monkeypatch.setenv("PEACE_GOOGLE_CLIENT_ID", "")
    assert browser.get("/auth/google").json() == {"available": False}


def test_start_asks_only_for_email_with_pkce(browser):
    q = start(browser)
    assert q["client_id"] == CLIENT and q["scope"] == "openid email" and q["response_type"] == "code"
    assert q["redirect_uri"] == "http://127.0.0.1:8421/auth/google/callback"
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43 and q["state"] and q["nonce"]


def test_first_account_becomes_owner_and_gets_a_session(browser, monkeypatch, session):
    q, seen, r = sign_in(browser, monkeypatch)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert seen["code"] == "c0de" and seen["redirect_uri"] == q["redirect_uri"] and seen["verifier"]
    assert browser.get("/auth/session").status_code == 204
    assert browser.get("/auth/google").json() == {"available": True}
    owner = session.get(google.Preference, "owner").value
    assert owner == {"sub": "111", "email": "ana@gmail.com"}


def test_first_owner_needs_a_browser_that_holds_the_token(browser, monkeypatch):
    """Another user or process on the PC must not claim a fresh install by signing in with Google first."""
    other = stranger()
    _, _, r = sign_in(other, monkeypatch, sub="666", email="eve@gmail.com")
    assert r.headers["location"] == "/entrar?erro=primeiro"
    assert other.get("/auth/session").status_code == 401


def test_another_account_is_refused(browser, monkeypatch):
    sign_in(browser, monkeypatch)
    browser = stranger()
    _, _, r = sign_in(browser, monkeypatch, sub="222", email="eve@gmail.com")
    assert r.headers["location"] == "/entrar?erro=conta"
    assert browser.get("/auth/session").status_code == 401


def test_same_owner_signs_in_again_from_a_new_browser(browser, monkeypatch):
    sign_in(browser, monkeypatch)
    browser = stranger()
    _, _, r = sign_in(browser, monkeypatch, email="ana.new@gmail.com")  # email changed, same Google account
    assert r.headers["location"] == "/"


@pytest.mark.parametrize("bad", [{"aud": "someone-else"}, {"iss": "https://evil.example"}, {"exp": 1},
                                 {"nonce": "replayed"}, {"email_verified": False}])
def test_untrustworthy_id_token_is_refused(browser, monkeypatch, bad):
    browser = stranger()
    _, _, r = sign_in(browser, monkeypatch, **bad)
    assert r.headers["location"] == "/entrar?erro=pedido"
    assert browser.get("/auth/session").status_code == 401


def test_garbage_input_redirects_instead_of_crashing(browser, monkeypatch):
    start(browser)
    assert browser.get("/auth/google/callback", params={"code": "c", "state": "çã"}).headers["location"] == "/entrar?erro=pedido"
    assert google._unsign("e30.çã") is None and google._unsign("bm90IGpzb24.x") is None
    assert not google._trusted(["not", "a", "dict"], "n") and not google._trusted({"exp": "soon"}, "n")


def test_redirect_uri_ignores_userinfo_in_the_host_header(browser):
    r = browser.get("/auth/google/start", headers={"host": "evil.com@localhost:8421"})
    if r.headers["location"].startswith("https://accounts.google.com"):
        assert "evil" not in r.headers["location"]


def test_wrong_state_or_missing_flow_is_refused(browser, monkeypatch):
    start(browser)
    monkeypatch.setattr(google, "exchange", lambda *a: pytest.fail("must not reach Google"))
    assert browser.get("/auth/google/callback", params={"code": "c", "state": "forged"}).headers["location"] == "/entrar?erro=pedido"
    browser = stranger()
    assert browser.get("/auth/google/callback", params={"code": "c", "state": "x"}).headers["location"] == "/entrar?erro=pedido"


def test_cancelled_consent_and_network_failure(browser, monkeypatch):
    start(browser)
    assert browser.get("/auth/google/callback", params={"error": "access_denied"}).headers["location"] == "/entrar?erro=cancelado"
    q = start(browser)

    def offline(*a):
        raise google.httpx.ConnectError("offline")
    monkeypatch.setattr(google, "exchange", offline)
    r = browser.get("/auth/google/callback", params={"code": "c", "state": q["state"]})
    assert r.headers["location"] == "/entrar?erro=ligacao"


def test_not_offered_without_a_client_or_off_loopback(browser, monkeypatch):
    remote = TestClient(api.app, base_url="http://peace.example.pt:8420", follow_redirects=False)
    assert remote.get("/auth/google/start").headers["location"] == "/entrar?erro=endereco"
    monkeypatch.setenv("PEACE_GOOGLE_CLIENT_ID", "")
    assert browser.get("/auth/google/start").headers["location"] == "/entrar?erro=indisponivel"


def test_token_login_still_works():
    browser = stranger()
    assert browser.post("/auth/login", json={"token": TOKEN}).status_code == 204
    assert browser.get("/auth/session").status_code == 204


def test_account_shows_the_owner_to_a_signed_in_browser(browser, monkeypatch):
    assert stranger().get("/auth/google/account").status_code == 401
    assert browser.get("/auth/google/account").json() == {"available": True, "email": None}
    sign_in(browser, monkeypatch)
    assert browser.get("/auth/google/account").json() == {"available": True, "email": "ana@gmail.com"}

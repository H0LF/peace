"""The ChatGPT subscription provider: device login, refresh, the Responses format, and failures that never leak a token."""

import base64
import json
import time
from datetime import UTC, datetime

import httpx
import pytest

from peace import assistant, chatgpt, providers
from peace.models import AssistantMessage

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)


def jwt(exp_in=3600, account="acct-1"):
    claims = {"exp": time.time() + exp_in, "https://api.openai.com/auth": {"chatgpt_account_id": account}}
    return "h." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".s"


def sse(*events):
    return "\n".join(f"data: {json.dumps(e)}\n" for e in events)


class Server:
    """OpenAI, as far as PEACE can tell."""

    def __init__(self):
        self.seen = []
        self.finished = False
        self.refreshes = 0
        self.answers = [sse({"type": "response.output_text.delta", "delta": "Olá"}, {"type": "response.completed", "response": {"output": []}})]
        self.status = 200

    def __call__(self, request):
        self.seen.append(request)
        path = request.url.path
        if path.endswith("/deviceauth/usercode"):
            return httpx.Response(200, json={"user_code": "ABCD-EFGH", "device_auth_id": "dev", "interval": "1"})
        if path.endswith("/deviceauth/token"):
            if not self.finished:
                return httpx.Response(403)
            return httpx.Response(200, json={"authorization_code": "code", "code_verifier": "ver", "code_challenge": "x"})
        if path.endswith("/oauth/token"):
            form = dict(p.split("=", 1) for p in request.content.decode().split("&"))
            if form["grant_type"] == "refresh_token":
                self.refreshes += 1
                if form["refresh_token"] == "dead":
                    return httpx.Response(401, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": jwt(), "refresh_token": f"r{self.refreshes}"})
        if path.endswith("/models"):
            return httpx.Response(200, json={"models": [
                {"slug": "gpt-b", "priority": 2}, {"slug": "gpt-a", "priority": 1, "display_name": "GPT A"}, {"slug": "secret", "visibility": "hide"}]})
        if path.endswith("/responses"):
            if self.status != 200:
                return httpx.Response(self.status, json={"error": {"code": "usage_limit_reached"}})
            return httpx.Response(200, content=self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])
        return httpx.Response(404)


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("PEACE_SECRET_STORE", "file")
    monkeypatch.setattr(chatgpt, "data_dir", lambda: tmp_path)
    store = {}
    monkeypatch.setattr(providers.secret_store, "get", store.get)
    monkeypatch.setattr(providers.secret_store, "put", lambda n, v: store.__setitem__(n, v) or "file")
    monkeypatch.setattr(providers.secret_store, "delete", lambda n: store.pop(n, None))
    s = Server()
    monkeypatch.setattr(chatgpt, "_transport", httpx.MockTransport(s))
    providers._down.clear()
    chatgpt._pending.clear()
    yield s
    providers._down.clear()


def logged_in(access=None, refresh="r0"):
    providers.save_key(chatgpt.ID, json.dumps({"access": access or jwt(), "refresh": refresh}))


def test_login_waits_for_the_student_then_stores_the_tokens(server):
    shown = chatgpt.start_login()
    assert shown == {"url": "https://auth.openai.com/codex/device", "code": "ABCD-EFGH", "interval": 3}
    assert chatgpt.poll_login() is False and not providers.has_key(chatgpt.ID)
    server.finished = True
    assert chatgpt.poll_login() is True and providers.has_key(chatgpt.ID)
    with pytest.raises(providers.ProviderError) as e:
        chatgpt.poll_login()
    assert e.value.code == "expired"


def test_an_expired_token_is_refreshed_once_and_the_new_one_kept(server):
    logged_in(jwt(exp_in=-10))
    assert [m["id"] for m in chatgpt.list_models()] == ["gpt-a", "gpt-b"]
    assert server.refreshes == 1
    assert json.loads(providers.get_key(chatgpt.ID))["refresh"] == "r1"
    chatgpt.list_models()
    assert server.refreshes == 1


def test_a_revoked_login_asks_to_log_in_again_without_the_token_in_the_error(server):
    logged_in(jwt(exp_in=-10), refresh="dead")
    with pytest.raises(httpx.ConnectError) as e:
        chatgpt.chat("gpt-a", {"messages": [{"role": "user", "content": "oi"}]}, 5)
    assert str(e.value) == "login" and providers.error(chatgpt.ID) == "login"
    assert "dead" not in str(e.value)


def test_a_chat_goes_out_in_the_responses_format_and_comes_back_in_ollamas(server):
    logged_in()
    r = providers.chat(chatgpt.ID, "gpt-a", {"messages": [{"role": "system", "content": "Sê breve"}, {"role": "user", "content": "oi"}]}, 5)
    assert r.json() == {"message": {"content": "Olá", "tool_calls": [], "sources": []}}
    sent = server.seen[-1]
    body = json.loads(sent.content)
    assert body["instructions"] == "Sê breve" and body["stream"] is True and body["store"] is False
    assert body["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "oi"}]}]
    assert sent.headers["ChatGPT-Account-ID"] == "acct-1" and sent.headers["originator"] == "peace"


def test_tools_and_a_schema_are_translated_and_a_tool_call_comes_back(server):
    logged_in()
    server.answers = [sse({"type": "response.completed", "response": {"output": [
        {"type": "function_call", "call_id": "c1", "name": "add_task", "arguments": "{\"t\": 1}"}]}})]
    tool = {"type": "function", "function": {"name": "add_task", "description": "d", "parameters": {"type": "object"}}}
    message = providers.chat(chatgpt.ID, "gpt-a", {"messages": [{"role": "user", "content": "x"}], "tools": [tool], "format": {"type": "object"}}, 5).json()["message"]
    assert message["tool_calls"] == [{"id": "c1", "type": "function", "function": {"name": "add_task", "arguments": "{\"t\": 1}"}}]
    body = json.loads(server.seen[-1].content)
    assert body["tools"][0]["name"] == "add_task" and body["text"]["format"]["schema"] == {"type": "object"}


def test_reaching_the_plans_limit_reads_as_quota(server):
    logged_in()
    server.status = 429
    with pytest.raises(httpx.ConnectError) as e:
        chatgpt.chat("gpt-a", {"messages": [{"role": "user", "content": "x"}]}, 5)
    assert str(e.value) == "quota"


def test_an_answer_cut_short_is_never_used(server):
    logged_in()
    server.answers = [sse({"type": "response.output_text.delta", "delta": "meia"})]
    with pytest.raises(httpx.ConnectError):
        chatgpt.chat("gpt-a", {"messages": [{"role": "user", "content": "x"}]}, 5)


def test_the_api_starts_and_finishes_a_login_and_refuses_a_pasted_key(server, client):
    assert client.put(f"/api/v1/ai/providers/{chatgpt.ID}/key", json={"key": "sk-" + "x" * 30}).status_code == 400
    row = next(p for p in client.get("/api/v1/ai").json()["providers"] if p["id"] == chatgpt.ID)
    assert row["login"] is True and row["connected"] is False
    assert client.post(f"/api/v1/ai/providers/{chatgpt.ID}/login").json()["code"] == "ABCD-EFGH"
    assert client.post(f"/api/v1/ai/providers/{chatgpt.ID}/login/poll").json() == {"connected": False}
    server.finished = True
    assert client.post(f"/api/v1/ai/providers/{chatgpt.ID}/login/poll").json() == {"connected": True}
    assert client.get(f"/api/v1/ai/providers/{chatgpt.ID}/models").json()[0]["id"] == "gpt-a"
    assert client.post("/api/v1/ai/providers/openai/login").status_code == 404


def test_web_search_is_asked_for_and_its_sources_come_back_once(server):
    logged_in()
    note = {"type": "url_citation", "url": "https://example.pt/a", "title": "Fonte"}
    item = {"type": "message", "content": [{"type": "output_text", "text": "Resposta", "annotations": [note]}]}
    server.answers = [sse({"type": "response.output_item.done", "item": item}, {"type": "response.completed", "response": {"output": [item]}})]
    message = providers.chat(chatgpt.ID, "gpt-a", {"messages": [{"role": "user", "content": "x"}], "web_search": True}, 5).json()["message"]
    assert message["content"] == "Resposta" and message["sources"] == [{"title": "Fonte", "url": "https://example.pt/a"}]
    assert json.loads(server.seen[-1].content)["tools"] == [{"type": "web_search", "external_web_access": True}]


def test_a_stream_that_never_finishes_stops_at_the_deadline():
    with pytest.raises(providers.ProviderError):
        chatgpt._read_stream(["data: {}"], deadline=0)


def test_every_subscription_model_can_be_chosen_and_named_loosely(session, server, monkeypatch):
    from peace import brain
    logged_in()
    rows = [{"slug": m} for m in ("gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-luna")]
    monkeypatch.setattr(chatgpt, "list_models", lambda: [{"id": r["slug"], "name": r["slug"], "price": None} for r in rows])
    providers._offered.clear()
    monkeypatch.setattr(brain.llm, "installed_models", lambda: [])
    assert {c["model"] for c in brain.choices(session) if c["provider"] == chatgpt.ID} == {r["slug"] for r in rows}
    assert brain.parse("muda para gpt 5 luna") == ("switch", "gpt 5 luna")
    assert brain.parse("altera o teu cerebro para o gpt 5 luna") == ("switch", "gpt 5 luna")
    assert brain.run(session, "switch", "gpt 5 luna", "muda para gpt 5 luna").startswith("Mudei para gpt-5.6-luna")
    assert brain.active(session)["model"] == "gpt-5.6-luna"
    brain.run(session, "switch", "gpt 6 luna", "muda para gpt 6 luna")
    assert brain.active(session)["model"] == "gpt-6-luna"  # never the 5.6, which also has a 6 in it
    assert "gpt-6-sol" in brain.run(session, "switch", "sol", "muda para sol")  # two: it asks which
    assert "subscrição" in brain.run(session, "which")
    providers.remove_key(chatgpt.ID)
    assert not [c for c in brain.choices(session) if c["provider"] == chatgpt.ID]


def usage_payload(path_used=7):
    return {"plan_type": "plus", "rate_limit": {"limit_reached": False,
            "primary_window": {"used_percent": path_used, "limit_window_seconds": 18000, "reset_at": 1793726093},
            "secondary_window": {"used_percent": 3, "limit_window_seconds": 604800, "reset_at": 1793821243}}}


def test_the_plans_limits_are_read_and_kept_a_minute(server, monkeypatch, client):
    logged_in()
    chatgpt._usage.clear()
    seen = []
    real = server.__call__

    def handler(request):
        if request.url.path.endswith("/wham/usage"):
            seen.append(request)
            return httpx.Response(200, json=usage_payload())
        return real(request)

    monkeypatch.setattr(chatgpt, "_transport", httpx.MockTransport(handler))
    got = client.get(f"/api/v1/ai/providers/{chatgpt.ID}/usage").json()
    assert got["plan"] == "plus" and [w["used"] for w in got["windows"]] == [7, 3] and got["windows"][0]["seconds"] == 18000
    client.get(f"/api/v1/ai/providers/{chatgpt.ID}/usage")
    assert len(seen) == 1 and seen[0].headers["ChatGPT-Account-ID"] == "acct-1"
    assert client.get("/api/v1/ai/providers/openai/usage").status_code == 404
    chatgpt._usage.clear()


def test_asking_in_words_says_whats_left_and_never_fails_loudly(session, server, monkeypatch):
    chatgpt._usage.clear()
    assert "ainda não ligaste" in assistant.cmd_plan_usage(session, NOW).text
    logged_in()
    monkeypatch.setattr(chatgpt, "_transport", httpx.MockTransport(lambda r: httpx.Response(200, json=usage_payload())))
    text = assistant.cmd_plan_usage(session, NOW).text
    assert "93% nas próximas 5 horas" in text and "97% esta semana" in text
    chatgpt._usage.clear()
    monkeypatch.setattr(chatgpt, "_transport", httpx.MockTransport(lambda r: httpx.Response(500)))
    assert "Tenta daqui a pouco" in assistant.cmd_plan_usage(session, NOW).text
    chatgpt._usage.clear()

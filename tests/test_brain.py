"""AI providers: the student's key, what may leave this computer, switching model by button and by words."""

import json
import stat

import httpx
import pytest
from sqlalchemy.orm import Session

from peace import brain, llm, providers, secret_store
from peace.assistant import _call
from peace.db import get_engine
from peace.models import Preference

KEY = "sk-test-" + "a1b2c3d4" * 4


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PEACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PEACE_SECRET_STORE", "file")
    providers._down.clear()
    monkeypatch.setattr(llm, "installed_models", lambda: ["gpt-oss:20b", "qwen3:4b"])
    monkeypatch.setitem(llm._reachable, "sizes", {"gpt-oss:20b": 13_000_000_000, "qwen3:4b": 2_600_000_000})
    monkeypatch.setattr(llm, "llm_reachable", lambda purpose="local": True)
    yield tmp_path


class Cloud:
    """A provider on the wire: records requests, answers what the test says."""

    def __init__(self, monkeypatch):
        self.calls, self.status, self.body = [], 200, {"choices": [{"message": {"content": "olá"}}]}
        self.models = {"data": [{"id": "gpt-6"}, {"id": "gpt-6-mini"}, {"id": "whisper-1"}, {"id": "text-embedding-3"}]}
        monkeypatch.setattr(httpx, "get", self.get)
        monkeypatch.setattr(httpx, "post", self.post)

    def _answer(self, url, body):
        return httpx.Response(self.status, json=body, request=httpx.Request("GET", url))

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers, None))
        return self._answer(url, self.models)

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, headers, json))
        return self._answer(url, self.body)


@pytest.fixture
def cloud(monkeypatch):
    return Cloud(monkeypatch)


def connect(client, cloud, model="gpt-6"):
    assert client.put("/api/v1/ai/providers/openai/key", json={"key": KEY}).status_code == 200
    assert client.put("/api/v1/ai/active", json={"provider": "openai", "model": model}).status_code == 200


def prefs():
    with Session(get_engine()) as s:
        row = s.get(Preference, "assistant")
        return dict(row.value) if row else {}


# --- the key ------------------------------------------------------------------------------------------------------


def test_the_key_file_is_encrypted_private_and_tied_to_the_api_token(store, monkeypatch):
    assert secret_store.put("x", KEY) == "file"
    path = store / "secrets.enc"
    assert KEY not in path.read_text() and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert secret_store.get("x") == KEY
    secret_store.delete("x")
    assert secret_store.get("x") is None
    secret_store.put("x", KEY)
    other = type(secret_store.get_settings())(api_token="o" * 40, timezone="UTC", log_level="INFO", host="", port=0)
    monkeypatch.setattr(secret_store, "get_settings", lambda: other)
    assert secret_store.get("x") is None  # token rotated: unreadable, reported as no key, no crash


def test_a_keychain_that_hangs_or_breaks_falls_back_to_the_file(store, monkeypatch):
    monkeypatch.delenv("PEACE_SECRET_STORE")

    class Broken:
        priority = 5

        def set_password(self, *a):
            raise RuntimeError(KEY)

        def get_password(self, *a):
            raise RuntimeError(KEY)

        def delete_password(self, *a):
            raise RuntimeError(KEY)
    monkeypatch.setattr(secret_store, "_keychain", lambda: Broken())
    assert secret_store.put("x", KEY) == "file"
    assert secret_store.get("x") == KEY


def test_a_working_keychain_holds_the_only_copy(store, monkeypatch):
    held = {}

    class Chain:
        priority = 5

        def set_password(self, service, name, value):
            held[name] = value

        def get_password(self, service, name):
            return held.get(name)

        def delete_password(self, service, name):
            del held[name]
    secret_store.put("x", "older")  # was in the file before a keychain appeared
    monkeypatch.setattr(secret_store, "_keychain", lambda: Chain())
    assert secret_store.put("x", KEY) == "keychain"
    assert "x" not in json.loads((store / "secrets.enc").read_text())
    assert secret_store.get("x") == KEY


def test_a_rejected_key_is_not_kept_and_no_key_ever_comes_back(client, cloud, caplog):
    cloud.status = 401
    r = client.put("/api/v1/ai/providers/openai/key", json={"key": KEY})
    assert r.status_code == 400 and r.json()["detail"] == "invalid_key" and KEY not in r.text
    assert not providers.has_key("openai")
    cloud.status = 200
    r = client.put("/api/v1/ai/providers/openai/key", json={"key": KEY})
    assert r.status_code == 200 and KEY not in r.text
    assert cloud.calls[-1][1].startswith("https://api.openai.com/") and KEY in cloud.calls[-1][2]["Authorization"]
    assert KEY not in client.get("/api/v1/ai").text and KEY not in json.dumps(prefs()) and KEY not in caplog.text
    assert client.put("/api/v1/ai/providers/openai/key", json={"key": "short"}).status_code == 400
    assert client.put("/api/v1/ai/providers/nope/key", json={"key": KEY}).status_code == 404


def test_openrouter_checks_the_key_where_a_key_is_required(client, cloud):
    assert client.put("/api/v1/ai/providers/openrouter/key", json={"key": KEY}).status_code == 200
    assert cloud.calls[-1][1] == "https://openrouter.ai/api/v1/key"  # /models answers without any key


def test_removing_the_key_of_the_active_provider_returns_to_the_local_model(client, cloud):
    connect(client, cloud)
    r = client.delete("/api/v1/ai/providers/openai/key").json()
    assert r["active"]["provider"] == "" and not r["providers"][0]["connected"]
    assert not providers.has_key("openai") and prefs()["favourites"] == []


# --- choosing -----------------------------------------------------------------------------------------------------


def test_only_models_that_can_chat_are_listed_with_their_price(client, cloud):
    client.put("/api/v1/ai/providers/openai/key", json={"key": KEY})
    assert [m["id"] for m in client.get("/api/v1/ai/providers/openai/models").json()] == ["gpt-6", "gpt-6-mini"]
    cloud.models = {"data": [{"id": "a/x", "supported_parameters": ["tools"], "pricing": {"prompt": "0.000003"}},
                             {"id": "a/no-tools", "supported_parameters": []}]}
    assert client.get("/api/v1/ai/providers/openai/models").json() == [{"id": "a/x", "name": "a/x", "price": 3.0}]


def test_a_model_the_provider_does_not_offer_or_without_a_key_changes_nothing(client, cloud):
    assert client.put("/api/v1/ai/active", json={"provider": "openai", "model": "gpt-6"}).json()["detail"] == "no_key"
    client.put("/api/v1/ai/providers/openai/key", json={"key": KEY})
    assert client.put("/api/v1/ai/active", json={"provider": "openai", "model": "gpt-9"}).json()["detail"] == "not_offered"
    assert client.put("/api/v1/ai/active", json={"provider": "", "model": "nope"}).json()["detail"] == "not_installed"
    assert client.get("/api/v1/ai").json()["active"]["provider"] == ""


def test_switching_remembers_the_previous_model_and_keeps_the_local_choice(client, cloud):
    client.put("/api/v1/ai/active", json={"provider": "", "model": "qwen3:4b"})
    connect(client, cloud)
    r = client.get("/api/v1/ai").json()
    assert r["active"]["model"] == "gpt-6" and r["previous"] == {"provider": "", "model": "qwen3:4b"}
    assert {"provider": "openai", "model": "gpt-6", "size": None, "price": None} in r["choices"]
    client.put("/api/v1/preferences/assistant", json={"model": "gpt-oss:20b"})  # the older route leaves the cloud settings alone
    assert prefs()["cloud_model"] == "gpt-6" and prefs()["model"] == "gpt-oss:20b"


# --- what leaves this computer ------------------------------------------------------------------------------------


def to_local(monkeypatch):
    seen = []

    def request(method, path, **kw):
        seen.append(kw["json"]["model"])
        return httpx.Response(200, json={"message": {"content": "{}"}}, request=httpx.Request(method, "http://x"))
    monkeypatch.setattr(llm, "_request", request)
    monkeypatch.setattr(llm, "loaded_models", lambda: [{"name": "qwen3:4b"}])
    monkeypatch.setattr(llm, "local_model", lambda: "qwen3:4b")
    return seen


@pytest.mark.parametrize("purpose,goes_out", [("chat", True), ("documents", True), ("background", False), ("mail", False), ("local", False)])
def test_by_default_mail_and_background_reading_stay_on_this_computer(client, cloud, monkeypatch, purpose, goes_out):
    connect(client, cloud)
    seen = to_local(monkeypatch)
    cloud.calls.clear()
    llm._llm("POST", "/api/chat", purpose=purpose, timeout=1, json={"model": "gpt-6", "messages": [{"role": "user", "content": "x"}]})
    assert bool(cloud.calls) is goes_out
    assert seen == ([] if goes_out else ["qwen3:4b"])  # never the cloud model's name sent to Ollama


def test_the_student_can_turn_mail_on_and_chat_off(client, cloud, monkeypatch):
    connect(client, cloud)
    r = client.put("/api/v1/ai/share", json={"chat": False, "documents": True, "background": False, "mail": True}).json()
    assert r["share"]["mail"] is True
    assert llm.route("mail") == ("openai", "gpt-6") and llm.route("chat") is None


def test_every_module_states_its_purpose():
    from peace import answers, assistant, exams, mail_extract, mastery, practice, requirements, topics, workload
    assert mail_extract.ask_json.keywords["purpose"] == "mail" and mail_extract.llm_reachable.keywords["purpose"] == "mail"
    assert topics._llm.keywords["purpose"] == "background"
    assert assistant._llm.keywords["purpose"] == "chat"
    for m in (exams, mastery, practice, requirements, workload):
        assert m.ask_json.keywords["purpose"] == "documents"
    assert answers._llm.keywords["purpose"] == "documents"


def test_a_schedule_is_not_sent_away_to_be_reworded_and_found_mail_stays_out_of_chat(client, cloud, monkeypatch):
    connect(client, cloud)
    from peace import assistant
    from peace.models import AssistantMessage
    monkeypatch.setattr(assistant, "llm_reachable", lambda: True)
    monkeypatch.setattr(assistant, "agent", lambda *a: ("day", {}, False))  # a plain request the keywords read too
    cloud.calls.clear()
    client.post("/api/v1/assistant/ask", json={"text": "o que tenho hoje?"})
    assert not [c for c in cloud.calls if c[0] == "POST"]
    with Session(get_engine()) as s:
        history = [AssistantMessage(role="assistant", content="SEGREDO do email", intent="search_mail"),
                   AssistantMessage(role="assistant", content="olá", intent="help")]
        assistant.chat(s, "conversa", history)
    sent = json.dumps(cloud.calls[-1][3], ensure_ascii=False)
    assert "SEGREDO" not in sent and "olá" in sent


# --- the wire ------------------------------------------------------------------------------------------------------


def test_a_request_is_translated_without_ollamas_options_and_the_answer_back(client, cloud):
    connect(client, cloud)
    tools = [{"type": "function", "function": {"name": "help", "parameters": {}}}]
    cloud.body = {"choices": [{"message": {"content": None, "tool_calls": [{"function": {"name": "day", "arguments": '{"offset": "1"}'}}]}}]}
    r = llm._llm("POST", "/api/chat", purpose="chat", timeout=9, json={
        "model": "x", "stream": False, "think": "low", "keep_alive": "30m", "options": {"num_ctx": 8192, "temperature": 0},
        "messages": [{"role": "user", "content": "amanhã"}], "tools": tools})
    sent = cloud.calls[-1][3]
    assert sent == {"model": "gpt-6", "stream": False, "messages": [{"role": "user", "content": "amanhã"}], "tools": tools}
    assert _call(r.json()["message"]["tool_calls"][0]) == ("day", {"offset": 1})  # a tool call's arguments arrive as text here


def test_ask_json_uses_a_schema_and_retries_in_words_for_a_model_that_refuses_it(client, cloud, monkeypatch):
    connect(client, cloud)
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    cloud.body = {"choices": [{"message": {"content": '```json\n{"a": 1}\n```'}}]}
    assert llm.ask_json("s", "u", schema, purpose="chat") == {"a": 1}
    assert cloud.calls[-1][3]["response_format"]["json_schema"]["schema"] == schema
    answers = iter([400, 200])

    def post(url, headers=None, json=None, timeout=None):
        cloud.calls.append(("POST", url, headers, json))
        return httpx.Response(next(answers), json=cloud.body, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    assert llm.ask_json("s", "u", schema, purpose="chat") == {"a": 1}
    retry = cloud.calls[-1][3]
    assert "response_format" not in retry and "esquema" in retry["messages"][0]["content"]


@pytest.mark.parametrize("status,body,code", [(401, {}, "invalid_key"), (402, {}, "quota"), (429, {"error": {"code": "insufficient_quota"}}, "quota"),
                                              (429, {}, "rate"), (500, {}, "network"), (404, {}, "model")])
def test_a_failed_call_means_try_later_not_a_bad_document_and_shows_the_reason(client, cloud, monkeypatch, status, body, code):
    connect(client, cloud)
    monkeypatch.setattr(llm, "llm_reachable", lambda purpose="local": True)
    cloud.status, cloud.body = status, body
    monkeypatch.setattr(llm, "mark_unreachable", lambda: None)
    assert llm.ask_json("s", "u", {}, purpose="chat") is None  # None: read it again later; {} would mark it as read
    assert providers.error("openai") == code and not providers.usable("openai")
    assert client.get("/api/v1/ai").json()["providers"][0]["error"] == code
    client.put("/api/v1/ai/providers/openai/key", json={"key": KEY}) if status != 401 else None


def test_a_network_failure_does_not_leak_the_key(client, cloud, monkeypatch, caplog):
    connect(client, cloud)

    def down(*a, **k):
        raise httpx.ConnectError("boom")
    monkeypatch.setattr(httpx, "post", down)
    with pytest.raises(httpx.ConnectError) as e:
        llm._llm("POST", "/api/chat", purpose="chat", timeout=1, json={"messages": []})
    assert KEY not in str(e.value) and KEY not in caplog.text and providers.error("openai") == "network"


# --- in words ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("Peace, change your brain.", ("list", "")),
    ("muda de cérebro", ("list", "")),
    ("Peace, switch to GPT.", ("switch", "gpt")),
    ("Peace, switch your brain to GPT.", ("switch", "gpt")),
    ("muda para o gpt", ("switch", "gpt")),
    ("Use my OpenRouter model.", ("switch", "openrouter")),
    ("usa o modelo local", ("switch", "local")),
    ("muda o modelo para qwen3:4b", ("switch", "qwen3:4b")),
    ("Change to a faster model.", ("faster", "")),
    ("muda para um modelo mais rápido", ("faster", "")),
    ("Switch to the most capable model available.", ("best", "")),
    ("usa o melhor modelo", ("best", "")),
    ("Which brain are you currently using?", ("which", "")),
    ("que modelo estás a usar?", ("which", "")),
    ("qual é o teu cérebro?", ("which", "")),
    ("Go back to the previous model.", ("previous", "")),
    ("volta ao modelo anterior", ("previous", "")),
])
def test_commands_about_the_model_are_recognised_in_both_languages(text, expected):
    assert brain.parse(text) == expected


@pytest.mark.parametrize("text", [
    "muda a aula de IA para sexta", "muda a reunião para amanhã", "o que é o modelo relacional?", "como se usa o modelo relacional",
    "usa o gpt para resumir isto", "volta à aula anterior", "explica o modelo OSI", "passa o teste para terça", "adiciona uma tarefa de IA",
    "qual é o melhor modelo de dados para isto?", "o que tenho hoje?",
])
def test_ordinary_requests_are_left_alone(text):
    assert brain.parse(text) is None


def ask(client, text):
    return client.post("/api/v1/assistant/ask", json={"text": text}).json()


def test_switching_by_voice_confirms_only_what_the_backend_did(client, cloud):
    connect(client, cloud)
    client.put("/api/v1/ai/active", json={"provider": "", "model": "qwen3:4b"})
    r = ask(client, "muda para gpt-6")
    assert r["intent"] == "model" and r["reply"] == "Mudei para gpt-6 (OpenAI). A conversa mantém-se."
    assert client.get("/api/v1/ai").json()["active"] == {"provider": "openai", "model": "gpt-6", "reachable": True, "error": None}
    assert "Estou a usar gpt-6" in ask(client, "que modelo estás a usar?")["reply"]
    assert ask(client, "muda para gpt-6")["reply"].startswith("Já estou a usar")
    assert "qwen3:4b" in ask(client, "volta ao modelo anterior")["reply"]
    assert client.get("/api/v1/ai").json()["active"]["model"] == "qwen3:4b"


def test_an_ambiguous_name_is_asked_about_and_an_unknown_one_is_never_invented(client, cloud):
    connect(client, cloud)
    client.put("/api/v1/ai/active", json={"provider": "openai", "model": "gpt-6-mini"})
    before = client.get("/api/v1/ai").json()["active"]["model"]
    r = ask(client, "switch to gpt")["reply"]
    assert "gpt-oss:20b" in r and "gpt-6-mini" in r
    assert "não" in ask(client, "muda o modelo para gemini-9").get("reply").lower()
    assert client.get("/api/v1/ai").json()["active"]["model"] == before


def test_a_provider_not_connected_is_explained(client, cloud):
    assert "You haven't connected OpenRouter" in ask(client, "use my openrouter model")["reply"]
    assert "Ainda não ligaste o OpenRouter" in ask(client, "usa o openrouter")["reply"]


def test_faster_and_best_pick_among_what_is_configured(client, cloud):
    connect(client, cloud)
    client.put("/api/v1/ai/active", json={"provider": "openai", "model": "gpt-6-mini"})
    client.put("/api/v1/ai/active", json={"provider": "", "model": "gpt-oss:20b"})
    assert "gpt-6-mini" in ask(client, "change to a faster model")["reply"]
    assert "gpt-6 (OpenAI)" in ask(client, "switch to the most capable model")["reply"]


def test_a_reply_follows_the_language_it_was_asked_in(client, cloud):
    assert ask(client, "which brain are you using?")["reply"].startswith("I'm using")
    assert ask(client, "que modelo estás a usar?")["reply"].startswith("Estou a usar")


def test_the_image_reader_is_not_offered_as_a_brain(client, monkeypatch):
    monkeypatch.setattr(llm, "installed_models", lambda: ["qwen3:4b", llm.VISION_MODEL])
    assert [c["model"] for c in client.get("/api/v1/ai").json()["choices"]] == ["qwen3:4b"]


def test_replies_follow_the_chosen_language(client, cloud, session):
    session.add(Preference(key="ui", value={"language": "en"}))
    session.commit()
    connect(client, cloud)
    client.put("/api/v1/ai/active", json={"provider": "", "model": "qwen3:4b"})
    assert ask(client, "switch to gpt-6")["reply"] == "Switched to gpt-6 (OpenAI). Your conversation is preserved."

"""Which AI PEACE uses: connecting providers, choosing the model, and the spoken or typed commands that change it.

Changing model is an application action, decided by rules here and confirmed by the backend, never by a model's words.
The conversation lives in the database, so it survives any change of model.
"""

import re
import unicodedata
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from peace import llm, providers, secret_store
from peace.auth import require_token
from peace.db import get_session
from peace.models import Preference

router = APIRouter(prefix="/api/v1/ai", dependencies=[Depends(require_token)])
DB = Depends(get_session)
LOCAL = ""  # the provider id of the model on this computer
MODEL_NAME = r"^[A-Za-z0-9._:/@+-]*$"
FAST_NAME = re.compile(r"mini|nano|flash|haiku|lite|small|fast|instant", re.I)
RICH_NAME = re.compile(r"opus|pro\b|max\b|large|sonnet|thinking|-o\d|^o\d", re.I)


def language(session: Session) -> str:
    row = session.get(Preference, "ui")
    return "en" if row and row.value.get("language") == "en" else "pt"


def _prefs(session: Session) -> dict[str, Any]:
    row = session.get(Preference, "assistant")
    return dict(row.value) if row else {}


def _save(session: Session, value: dict[str, Any]) -> None:
    row = session.get(Preference, "assistant")
    if row:
        row.value = value
    else:
        session.add(Preference(key="assistant", value=value))
    session.commit()
    llm.forget_model_check()


def choices(session: Session) -> list[dict[str, Any]]:
    """What the student can switch to now: models installed on this computer and those chosen from connected providers."""
    prefs = _prefs(session)
    llm.llm_reachable()
    sizes = llm._reachable.get("sizes", {})
    # The image reader cannot pick PEACE's commands: it only ever reads exam photos.
    out = [{"provider": LOCAL, "model": m, "size": sizes.get(m, 0), "price": None} for m in llm.installed_models() if m != llm.VISION_MODEL]
    for f in prefs.get("favourites", []):
        if f.get("provider") in providers.PROVIDERS and providers.has_key(f["provider"]):
            out.append({"provider": f["provider"], "model": f["model"], "size": None, "price": f.get("price")})
    # A subscription has no per-use cost and few models: all of them can be chosen. A key's long priced list stays in Settings.
    for p in providers.PROVIDERS.values():
        if p.login and providers.has_key(p.id):
            have = {c["model"] for c in out if c["provider"] == p.id}
            out += [{"provider": p.id, "model": m["id"], "size": None, "price": None} for m in providers.offered(p.id) if m["id"] not in have]
    return out


def active(session: Session) -> dict[str, str]:
    prefs = _prefs(session)
    if prefs.get("provider") in providers.PROVIDERS and prefs.get("cloud_model"):
        return {"provider": prefs["provider"], "model": prefs["cloud_model"]}
    return {"provider": LOCAL, "model": llm.local_model() if llm.llm_reachable() or llm.wanted_model() else ""}


class Unavailable(Exception):
    """The requested model cannot be used; `code` says why (not_installed, no_key, not_offered, network...)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def set_active(session: Session, provider: str, model: str) -> dict[str, str]:
    """Switch, after checking that this model really is available. Raises Unavailable and changes nothing otherwise."""
    prefs = _prefs(session)
    before = active(session)
    favourites = list(prefs.get("favourites", []))
    if provider == LOCAL:
        if model and model not in llm.installed_models():
            raise Unavailable("not_installed")
        prefs |= {"provider": LOCAL, "model": model}
    else:
        if provider not in providers.PROVIDERS or not providers.has_key(provider):
            raise Unavailable("no_key")
        if not any(f["provider"] == provider and f["model"] == model for f in favourites):
            try:
                offered = providers.list_models(provider)
            except providers.ProviderError as e:
                raise Unavailable(e.code) from e
            found = next((m for m in offered if m["id"] == model), None)
            if not found:
                raise Unavailable("not_offered")
            favourites.append({"provider": provider, "model": model, "price": found["price"]})
        prefs |= {"provider": provider, "cloud_model": model, "favourites": favourites}
    if before["model"] and (before["provider"], before["model"]) != (provider, model):
        prefs["previous"] = before
    _save(session, prefs)
    return {"provider": provider, "model": model}


# --- commands ---------------------------------------------------------------------------------------------------

BRAIN = r"(?:cerebro|brain|modelo|model|ia|ai|llm)"
VERB = r"(?:muda|mudar|muda-me|troca|trocar|altera|alterar|passa|passar|usa|usar|utiliza|switch|change|use|swap|go)"
FAMILY = r"(?:chat ?gpt|gpt[\w.:-]*(?: (?:\d[\w.]*|sol|luna|terra|astra|mini|pro|nano)){0,3}|subscricao|subscription|open ?ai|open ?router|ollama|local|qwen[\w.:-]*|llama[\w.:-]*|claude[\w.:-]*|gemini[\w.:-]*|mistral[\w.:-]*|deepseek[\w.:-]*)"
START = rf"^(?:quero |i want to |i'd like to |let's |vamos )?{VERB}\b"
WHICH = re.compile(rf"\b(?:qual|que|which|what)\b.*\b{BRAIN}\b.*\b(?:usar|usas|usando|using|atual|ativo|em uso|tens|have|current)\b"
                   rf"|\b(?:qual e o teu|qual o teu|what'?s your|what is your)\s+{BRAIN}\b|^current\s+{BRAIN}\b")
PREVIOUS = re.compile(rf"^(?:volta|voltar|regressa|go back|switch back|back|revert|reverte)\b.*\b{BRAIN}\s+anterior\b"
                      rf"|^(?:go back|switch back|back|revert)\b.*\b(?:previous|last)\s+{BRAIN}\b")
FASTER = re.compile(rf"{START}.*\b(?:mais rapido|faster|fastest|quickest|mais veloz)\b")
BEST = re.compile(rf"{START}.*\b(?:mais capaz|mais inteligente|mais poderoso|melhor|most capable|smartest|most powerful|best)\b.*\b{BRAIN}\b")
# Only determiners between the verb and the word: "muda a aula de IA para sexta" is about a class.
ASKED_BRAIN = re.compile(rf"{START}\s+(?:(?:de|o|a|teu|tua|your|my|meu|minha|the|this)\s+)*{BRAIN}\b")
TARGET = re.compile(r"\b(?:para|pra|to)\s+(?:o |a |my |meu |minha |the )?([\w.:/@+ -]+)$")
FILLER = re.compile(rf"\b(?:o|a|o teu|teu|tua|your|my|meu|minha|the|de|please|por favor)\b|\b{BRAIN}\b")
BY_NAME = re.compile(rf"^{VERB}\s+(?:(?:para|pra|to)\s+)?(?:o |a |my |meu |minha |the )?(?:{BRAIN}\s+)?({FAMILY})(?:\s+{BRAIN})?$")
GPT = re.compile(r"chat ?gpt")


def _fold(text: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()
    plain = re.sub(r"[?!.,;]+(\s|$)", " ", plain).strip()
    plain = re.sub(r"^(?:ola |hey |hi )?peace\s*", "", plain)
    return re.sub(r"^(?:por favor|please|podes|can you|could you)\s+", "", plain).strip()


def parse(text: str) -> tuple[str, str] | None:
    """(action, what) when the message asks about PEACE's own model; None for anything else."""
    plain = _fold(text)
    if WHICH.search(plain):
        return "which", ""
    if PREVIOUS.search(plain):
        return "previous", ""
    if FASTER.search(plain):
        return "faster", ""
    if BEST.search(plain):
        return "best", ""
    if named := BY_NAME.match(plain):
        return "switch", named[1]
    if ASKED_BRAIN.search(plain):
        if (target := TARGET.search(plain)) and not re.fullmatch(BRAIN, target[1].strip()):
            return "switch", FILLER.sub(" ", target[1]).strip()
        return "list", ""
    return None


def _label(c: dict[str, Any]) -> str:
    return f"{c['model']} ({providers.PROVIDERS[c['provider']].name})" if c["provider"] else c["model"]


def _speed(c: dict[str, Any]) -> float:
    """shortcut: guessed from name and size, since providers publish no speed. Bigger number: faster."""
    if c["provider"] == LOCAL:
        return 1 / (c["size"] or 1)  # all local ones rank by size among themselves
    return 2 if FAST_NAME.search(c["model"]) else 0.5 if RICH_NAME.search(c["model"]) else 1


def _power(c: dict[str, Any]) -> tuple[int, float]:
    """shortcut: name, then price or size, as a stand-in for capability. A cloud model outranks one on this computer."""
    tier = -1 if FAST_NAME.search(c["model"]) else 1 if RICH_NAME.search(c["model"]) else 0
    return (tier + (1 if c["provider"] else 0), c["price"] or c["size"] or 0)


def resolve(query: str, options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The options this name could mean; never a model that is not configured."""
    q = GPT.sub("gpt", query.lower().strip())
    exact = [c for c in options if c["model"].lower() == q]
    if exact:
        return exact
    words = re.findall(r"[a-z0-9]+", q)
    # Said aloud, or typed loosely: "gpt 6 luna" is gpt-6-luna, and "gpt 5 luna" can only be gpt-5.6-luna.
    same = [c for c in options if words and re.findall(r"[a-z0-9]+", c["model"].lower()) == words]
    if same:
        return same
    if q in ("subscricao", "subscription"):
        return [c for c in options if c["provider"] and providers.PROVIDERS[c["provider"]].login]
    if q in ("local", "ollama"):
        return [c for c in options if c["provider"] == LOCAL]
    if q.replace(" ", "") in providers.PROVIDERS:
        return [c for c in options if c["provider"] == q.replace(" ", "")]
    return [c for c in options if q and q in c["model"].lower()] or [c for c in options if words and set(words) <= set(re.findall(r"[a-z0-9]+", c["model"].lower()))]


ENGLISH = re.compile(r"\b(?:change|switch|use|which|what|your|my|the|go back|brain|faster|best|model)\b")
PORTUGUESE = re.compile(r"\b(?:muda|mudar|troca|usa|qual|que|teu|cerebro|modelo|volta|para)\b")


def run(session: Session, action: str, what: str = "", said: str = "") -> str:
    """Carries out a parsed command, in English if that is the chosen language or the one it was asked in. The reply claims
    a change only after the backend made it."""
    plain = _fold(said)
    en = language(session) == "en" or (bool(ENGLISH.search(plain)) and not PORTUGUESE.search(plain))
    say = (lambda pt, english: english) if en else (lambda pt, english: pt)
    current = active(session)
    options = choices(session)
    name = _label(current) if current["model"] else say("nenhum: só comandos internos", "none: built-in commands only")
    settings = say("Definições > IA e modelos", "Settings > AI & models")
    if action == "which":
        where = (say("na nuvem, com a tua subscrição", "in the cloud, with your subscription") if providers.PROVIDERS[current["provider"]].login
                 else say("na nuvem, com a tua chave", "in the cloud, with your key")) if current["provider"] else say("neste computador", "on this computer")
        return say(f"Estou a usar {name}, {where}.", f"I'm using {name}, {where}.") if current["model"] else say(f"Modelo: {name}.", f"Model: {name}.")

    if action == "previous":
        prev = _prefs(session).get("previous")
        if not prev:
            return say("Não tenho um modelo anterior para onde voltar.", "There's no previous model to go back to.")
        targets = [c for c in options if (c["provider"], c["model"]) == (prev["provider"], prev["model"])]
        if not targets:
            return say(f"O modelo anterior, {prev['model']}, já não está disponível.", f"The previous model, {prev['model']}, is no longer available.")
    elif action in ("faster", "best"):
        pick = max(options, key=_speed if action == "faster" else _power, default=None)
        targets = [pick] if pick else []
    elif action == "switch":
        targets = resolve(what, options)
        q = what.replace(" ", "").lower()
        if not targets and q in providers.PROVIDERS and not providers.has_key(q):
            return say(f"Ainda não ligaste o {providers.PROVIDERS[q].name}. Adiciona a tua chave em {settings}.",
                       f"You haven't connected {providers.PROVIDERS[q].name} yet. Add your key in {settings}.")
        if not targets:
            return say(f"Não tenho nenhum modelo «{what}» configurado. Vê os modelos em {settings}.",
                       f"I don't have a model called “{what}” set up. See {settings}.")
    else:
        targets = options
    if action == "list" or len(targets) != 1:
        if not targets:
            return say(f"Não há modelos disponíveis. Podes ligar um em {settings}.", f"No models are available. You can connect one in {settings}.")
        listing = ", ".join(_label(c) for c in targets)
        return say(f"Estou a usar {name}. Posso mudar para: {listing}. Diz, por exemplo, «muda para {targets[0]['model']}».",
                   f"I'm using {name}. I can switch to: {listing}. Say, for example, “switch to {targets[0]['model']}”.")
    target = targets[0]
    if (target["provider"], target["model"]) == (current["provider"], current["model"]):
        return say(f"Já estou a usar {_label(target)}.", f"I'm already using {_label(target)}.")
    try:
        set_active(session, target["provider"], target["model"])
    except Unavailable:
        return say(f"Não consegui mudar para {_label(target)}. Continuo com {name}.", f"I couldn't switch to {_label(target)}. I'm still on {name}.")
    return say(f"Mudei para {_label(target)}. A conversa mantém-se.", f"Switched to {_label(target)}. Your conversation is preserved.")


# --- endpoints --------------------------------------------------------------------------------------------------


class ActiveIn(BaseModel):
    provider: str = Field(LOCAL, max_length=30)
    model: str = Field("", max_length=200, pattern=MODEL_NAME)


class KeyIn(BaseModel):
    key: str  # no length rule here: a validation error would echo the rejected value back


class ShareIn(BaseModel):
    chat: bool
    documents: bool
    background: bool
    mail: bool


def _provider(provider_id: str) -> providers.Provider:
    if provider_id not in providers.PROVIDERS:
        raise HTTPException(404, "unknown_provider")
    return providers.PROVIDERS[provider_id]


def overview(session: Session) -> dict[str, Any]:
    prefs = _prefs(session)
    current = active(session)
    return {
        "active": current | {"reachable": llm.llm_reachable("chat"), "error": providers.error(current["provider"]) if current["provider"] else None},
        "previous": prefs.get("previous"),
        "choices": choices(session),
        "automatic": prefs.get("model", "") == "" and not current["provider"],
        "providers": [{"id": p.id, "name": p.name, "keys_url": p.keys_url, "login": p.login, "connected": providers.has_key(p.id), "error": providers.error(p.id)}
                      for p in providers.PROVIDERS.values()],
        "share": llm.SHARE_DEFAULTS | prefs.get("share", {}),
        "key_storage": secret_store.where(),
    }


@router.get("")
def get_overview(session: Session = DB):
    return overview(session)


@router.put("/active")
def put_active(body: ActiveIn, session: Session = DB):
    try:
        set_active(session, body.provider, body.model)
    except Unavailable as e:
        raise HTTPException(409, e.code) from e
    return overview(session)


@router.put("/providers/{provider_id}/key")
def put_key(provider_id: str, body: KeyIn, session: Session = DB):
    """Stored only once the provider has accepted it. The key never comes back in any response."""
    if _provider(provider_id).login:
        raise HTTPException(400, "invalid_key")
    key = body.key.strip()
    if not 10 <= len(key) <= 500 or not key.isascii() or any(c.isspace() for c in key):
        raise HTTPException(400, "invalid_key")
    try:
        providers.check_key(provider_id, key)
    except providers.ProviderError as e:
        raise HTTPException(400, e.code) from e
    providers.save_key(provider_id, key)
    return overview(session)


def _login_provider(provider_id: str) -> None:
    if not _provider(provider_id).login:
        raise HTTPException(404, "unknown_provider")


@router.post("/providers/{provider_id}/login")
def start_login(provider_id: str):
    """The code the student types on the provider's page."""
    _login_provider(provider_id)
    from peace import chatgpt
    try:
        return chatgpt.start_login()
    except providers.ProviderError as e:
        raise HTTPException(502 if e.code in ("network", "rate") else 400, e.code) from e


@router.post("/providers/{provider_id}/login/poll")
def poll_login(provider_id: str):
    _login_provider(provider_id)
    from peace import chatgpt
    try:
        return {"connected": chatgpt.poll_login()}
    except providers.ProviderError as e:
        raise HTTPException(409 if e.code == "expired" else 400, e.code) from e


@router.get("/providers/{provider_id}/usage")
def get_usage(provider_id: str):
    _login_provider(provider_id)
    from peace import chatgpt
    try:
        return chatgpt.usage()
    except providers.ProviderError as e:
        raise HTTPException(502 if e.code in ("network", "refused", "rate") else 400, e.code) from e


@router.delete("/providers/{provider_id}/key")
def delete_key(provider_id: str, session: Session = DB):
    """Forget the key. If that provider was in use, PEACE goes back to the model on this computer."""
    _provider(provider_id)
    providers.remove_key(provider_id)
    prefs = _prefs(session)
    if prefs.get("provider") == provider_id:
        prefs |= {"provider": LOCAL}
    prefs["favourites"] = [f for f in prefs.get("favourites", []) if f["provider"] != provider_id]
    if (prefs.get("previous") or {}).get("provider") == provider_id:
        prefs.pop("previous")
    _save(session, prefs)
    return overview(session)


@router.get("/providers/{provider_id}/models")
def get_models(provider_id: str):
    _provider(provider_id)
    try:
        return providers.list_models(provider_id)
    except providers.ProviderError as e:
        raise HTTPException(502 if e.code in ("network", "refused") else 400, e.code) from e


@router.delete("/providers/{provider_id}/models/{model:path}")
def delete_favourite(provider_id: str, model: str, session: Session = DB):
    _provider(provider_id)
    prefs = _prefs(session)
    if prefs.get("provider") == provider_id and prefs.get("cloud_model") == model:
        raise HTTPException(409, "in_use")
    prefs["favourites"] = [f for f in prefs.get("favourites", []) if (f["provider"], f["model"]) != (provider_id, model)]
    _save(session, prefs)
    return overview(session)


@router.put("/share")
def put_share(body: ShareIn, session: Session = DB):
    _save(session, _prefs(session) | {"share": body.model_dump()})
    return overview(session)

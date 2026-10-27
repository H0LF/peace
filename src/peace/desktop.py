"""Desktop actions asked through the assistant: the approved tools, the plan, its confirmation and the job.

The server never touches the desktop. A request becomes a short plan of tools from DESKTOP (the only ones the
model is offered); the PEACE desktop agent claims the plan, validates every step again against its own
schemas and permissions (desktop/peace_actions.py), runs it, and reports one result per step. Plans with a
step that can lose work wait for an explicit «sim». The reply the user gets is built from those results, so
PEACE never says it did something the agent did not confirm.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, StrictBool
from sqlalchemy import select
from sqlalchemy.orm import Session

from peace import routes
from peace import schemas as s
from peace.auth import require_token
from peace.db import get_session
from peace.models import AssistantMessage, Preference

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
DB = Depends(get_session)
NOW = Depends(routes.get_now)

CLAIM_WITHIN = timedelta(seconds=30)  # the agent polls every second; longer means it is not running
RUN_WITHIN = timedelta(minutes=3)
CONFIRM_WITHIN = timedelta(minutes=2)  # same window as the assistant's other confirmations
MAX_STEPS = 12
ROOTS = "vault (cofre do Obsidian), documentos, transferencias, secretaria, projetos"

_text = {"type": "string"}
_int = {"type": "integer"}
# Assistant command -> (agent tool, what it is for the model, arguments for the model).
DESKTOP: dict[str, tuple[str, str, dict[str, dict]]] = {
    "open_app": ("apps.launch", "Abrir uma aplicação instalada no PC (ex.: Obsidian, Brave, kitty, VS Code).", {"app": _text}),
    "find_files": ("files.search", "Procurar ficheiros no PC pelo nome.", {"query": _text}),
    "open_file": ("files.open", "Abrir no PC um ficheiro procurado pelo nome.", {"query": _text}),
    "move_file": ("files.move", f"Mover um ficheiro (procurado pelo nome) para uma pasta. Raízes: {ROOTS}.",
                  {"query": _text, "dest_root": _text, "dest_dir": _text}),
    "make_folder": ("files.mkdir", f"Criar uma pasta. Raízes: {ROOTS}.", {"root": _text, "path": _text}),
    "trash_file": ("files.trash", "Pôr no lixo um ficheiro procurado pelo nome (pede confirmação).", {"query": _text}),
    "focus_window": ("windows.focus", "Pôr em foco uma janela aberta (pela aplicação ou título).", {"window": _text}),
    "move_window": ("windows.move_to_workspace", "Mover uma janela para uma área de trabalho (número).",
                    {"window": _text, "workspace": _int}),
    "place_window": ("windows.place", "Pôr uma janela numa posição/tamanho em percentagem do ecrã (x, y, width, height).",
                     {"window": _text, "x": _int, "y": _int, "width": _int, "height": _int}),
    "close_window": ("windows.close", "Fechar uma janela (pede confirmação).", {"window": _text}),
    "switch_workspace": ("workspaces.switch", "Mudar para uma área de trabalho (número).", {"workspace": _int}),
    "apply_layout": ("layouts.apply", "Arrumar as janelas com uma disposição guardada (ex.: estudo, programacao).", {"name": _text}),
    "save_layout": ("layouts.save", "Guardar as janelas da área de trabalho atual como disposição.", {"name": _text}),
    "undo_layout": ("layouts.undo", "Repor as janelas como estavam antes da última disposição.", {}),
    "find_tabs": ("browser.tabs", "Procurar separadores abertos no Brave.", {"query": _text}),
    "open_tab": ("browser.open", "Abrir um endereço web (http/https) no Brave.", {"url": _text}),
    "focus_tab": ("browser.focus", "Mostrar um separador aberto do Brave.", {"query": _text}),
    "group_tabs": ("browser.group", "Agrupar separadores do Brave sob um nome.", {"query": _text, "title": _text}),
    "close_tabs": ("browser.close", "Fechar separadores do Brave (pede confirmação).", {"query": _text}),
}
# Agent tools that can lose work whatever their arguments; the agent also refuses them without confirmation.
DESTRUCTIVE_TOOLS = {"files.trash", "windows.close", "browser.close"}


def _pref(session: Session, key: str, lock: bool = False) -> dict[str, Any]:
    query = select(Preference).where(Preference.key == key)
    row = session.scalar(query.with_for_update() if lock else query)
    return dict(row.value) if row else {}


def _put(session: Session, key: str, value: dict[str, Any]) -> None:
    row = session.get(Preference, key)
    if row:
        row.value = value
    else:
        session.add(Preference(key=key, value=value))


def step(command: str, args: dict[str, Any]) -> dict[str, Any]:
    """An assistant command and its (model- or rule-given) arguments as an agent step."""
    tool, _, params = DESKTOP[command]
    return {"tool": tool, "args": {k: v for k, v in args.items() if k in params}}


def needs_confirmation(steps: list[dict]) -> bool:
    return any(x["tool"] in DESTRUCTIVE_TOOLS or x["args"].get("overwrite") for x in steps)


def _new(steps: list[dict], title: str, state: str, now: datetime, confirmed: bool = False) -> dict[str, Any]:
    return {"id": uuid.uuid4().hex, "title": title[:200], "steps": steps[:MAX_STEPS], "state": state, "confirmed": confirmed,
            "cancel": False, "results": [], "summary": None, "created_at": now.isoformat(),
            "expires_at": (now + (CONFIRM_WITHIN if state == "confirm" else CLAIM_WITHIN)).isoformat()}


def _live(job: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The job as it stands now: a plan nobody claimed or an agent that went silent is reported, not hidden."""
    if job.get("state") in ("pending", "confirm") and now >= datetime.fromisoformat(job["expires_at"]):
        job = job | {"state": "expired", "summary": "O agente do PC não pegou no pedido (está a correr?). Nada foi feito."
                     if job["state"] == "pending" else "A confirmação expirou. Nada foi feito."}
    elif job.get("state") == "running" and now >= datetime.fromisoformat(job["expires_at"]):
        job = job | {"state": "failed", "summary": summarize(job.get("results", []), len(job["steps"]))
                     + " O agente deixou de responder a meio."}
    return job


def create_job(session: Session, steps: list[dict], title: str, now: datetime) -> dict[str, Any]:
    current = _live(_pref(session, "desktop_job", lock=True), now)
    if current.get("state") in ("pending", "running"):
        raise HTTPException(409, "Já há uma ação no PC em curso.")
    job = _new(steps, title, "confirm" if needs_confirmation(steps) else "pending", now)
    _put(session, "desktop_job", job)
    _put(session, "desktop_job_follow_up", {})
    routes._audit(session, "desktop.plan", job=job["id"], tools=[x["tool"] for x in job["steps"]], confirm=job["state"] == "confirm")
    return job


def confirm_job(session: Session, job_id: str, now: datetime) -> dict[str, Any] | None:
    """«sim» to a plan waiting for it (or to the steps the agent refused without one): it may run now."""
    for key in ("desktop_job", "desktop_job_follow_up"):
        job = _live(_pref(session, key, lock=True), now)
        if job.get("id") == job_id and job.get("state") == "confirm":
            if key != "desktop_job" and _live(_pref(session, "desktop_job", lock=True), now).get("state") in ("pending", "running"):
                return None
            job |= {"state": "pending", "confirmed": True, "expires_at": (now + CLAIM_WITHIN).isoformat()}
            _put(session, "desktop_job", job)
            _put(session, "desktop_job_follow_up", {})
            routes._audit(session, "desktop.confirm", job=job_id)
            return job
    return None


def public(job: dict[str, Any]) -> dict[str, Any]:
    return {k: job.get(k) for k in ("id", "title", "state", "summary", "results", "created_at")} | {"steps": len(job.get("steps", []))}


def summarize(results: list[dict], planned: int) -> str:
    """What happened, from the agent's own results: done and verified, done without proof, or failed."""
    if not results:
        return "Nada foi feito."
    failed = sum(not r["ok"] for r in results)
    head = ("Feito." if not failed and all(r["verified"] for r in results) else
            "Feito, mas há passos que não consegui confirmar." if not failed else
            f"Fiz {len(results) - failed} de {planned} passos." if failed < len(results) else "Não consegui fazer nada.")
    if len(results) < planned:
        head += f" Ficaram {planned - len(results)} passos por fazer."
    parts = [r["message"] if r["ok"] else f"Falhou: {r['message']}" for r in results]
    return (head + " " + " ".join(parts))[:900]


class StepResult(s.In):
    tool: str = Field(max_length=40)
    ok: StrictBool
    verified: StrictBool
    code: str | None = Field(None, max_length=40)
    message: str = Field("", max_length=300)
    data: dict[str, Any] = Field(default_factory=dict)


class ProgressIn(s.In):
    results: list[StepResult] = Field(max_length=MAX_STEPS)
    final: StrictBool = False
    cancelled: StrictBool = False


def _small(data: dict[str, Any]) -> dict[str, Any]:
    """At most 20 listed items with short text fields: results are shown, not kept as data."""
    items = data.get("items") if isinstance(data.get("items"), list) else []
    return {"items": [{str(k)[:40]: str(v)[:200] for k, v in i.items()} for i in items[:20] if isinstance(i, dict)]} if items else {}


@router.get("/desktop/jobs/next")
def next_job(session: Session = DB, now: datetime = NOW):
    """The agent claims the pending plan; claiming happens under a row lock, so a plan runs once."""
    job = _live(_pref(session, "desktop_job", lock=True), now)
    if job.get("state") != "pending":
        return None
    job |= {"state": "running", "expires_at": (now + RUN_WITHIN).isoformat()}
    _put(session, "desktop_job", job)
    session.commit()
    return {"id": job["id"], "steps": job["steps"], "confirmed": job["confirmed"]}


@router.post("/desktop/jobs/{job_id}/progress")
def job_progress(job_id: str, body: ProgressIn, session: Session = DB, now: datetime = NOW):
    job = _live(_pref(session, "desktop_job", lock=True), now)
    if job.get("id") != job_id or job.get("state") != "running":
        raise HTTPException(409, "Esta ação já não está em curso.")
    results = [r.model_dump() | {"data": _small(r.data)} for r in body.results][: len(job["steps"])]
    job |= {"results": results, "expires_at": (now + RUN_WITHIN).isoformat()}
    if body.final or body.cancelled:
        planned, ok = len(job["steps"]), sum(r["ok"] for r in results)
        state = "cancelled" if body.cancelled else "done" if ok == planned == len(results) else "failed" if ok == 0 else "partial"
        summary = summarize(results, planned) + (" Cancelado a pedido." if state == "cancelled" else "")
        refused = [x for x, r in zip(job["steps"], results) if r["code"] == "confirmation_required"]
        if refused and state != "cancelled":
            follow = _new(refused, job["title"], "confirm", now)
            _put(session, "desktop_job_follow_up", follow)
            _put(session, "assistant_pending", {"name": "desktop_confirm", "args": {"job": follow["id"]}, "at": now.isoformat()})
            summary += f" Confirmas {len(refused)} passo(s) que podem apagar ou substituir algo? Responde «sim»."
        job |= {"state": state, "summary": summary}
        session.add(AssistantMessage(role="assistant", content=summary, source="desktop", intent="desktop_result",
                                     data={"items": [], "to": None, "via": "desktop", "job": job_id}))
        routes._audit(session, "desktop.result", job=job_id, state=state, ok=ok, steps=planned)
    _put(session, "desktop_job", job)
    session.commit()
    return {"cancel": bool(job.get("cancel"))}


@router.get("/desktop/jobs/{job_id}")
def job_status(job_id: str, session: Session = DB, now: datetime = NOW):
    job = _live(_pref(session, "desktop_job"), now)
    if job.get("id") != job_id:
        raise HTTPException(404, "Ação desconhecida ou substituída por outra.")
    return public(job)


@router.post("/desktop/jobs/{job_id}/cancel")
def cancel_job(job_id: str, session: Session = DB, now: datetime = NOW):
    job = _live(_pref(session, "desktop_job", lock=True), now)
    if job.get("id") != job_id:
        raise HTTPException(404, "Ação desconhecida ou substituída por outra.")
    if job["state"] in ("pending", "confirm"):
        job |= {"state": "cancelled", "summary": "Cancelado antes de começar. Nada foi feito."}
    elif job["state"] == "running":
        job["cancel"] = True  # the agent stops before its next step and reports what already ran
    _put(session, "desktop_job", job)
    routes._audit(session, "desktop.cancel", job=job_id)
    session.commit()
    return public(job)

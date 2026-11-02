"""One page of health for every part of PEACE (Settings → Diagnóstico), built from state that already exists.

Each component is {"id", "name", "state": ok|warn|error|off, "code", "detail"}. `code` is a stable identifier for a
problem (e.g. backup_stale), the same in the API, the dashboard and the alert, so a report can name it exactly.
Nothing here holds a token, a password, a file's contents or a URL with a query string.
"""

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from peace import llm, routes, sync
from peace.auth import require_token
from peace.db import get_session
from peace.models import AuditEvent, Preference
from peace.notifications import WORKER_SILENT, _add

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
BACKUP_DIR = Path(os.environ.get("PEACE_BACKUP_DIR", Path.home() / "peace-backups"))
BACKUP_KINDS = {"db": "peace-*.dump", "vault": "vault-*.tar.gz", "repo": "repo-*.bundle"}
BACKUP_LATE = timedelta(hours=36)  # daily at 03:30, plus a missed night
_backup_cache: dict[str, Any] = {"at": -1e9, "value": None}


def _c(id_: str, name: str, state: str, detail: str, code: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"id": id_, "name": name, "state": state, "code": code, "detail": detail} | extra


def backups(now: datetime) -> list[dict[str, Any]]:
    """The newest backup of each kind and how its last run ended (status-<kind>.json from scripts/backup.sh).
    Read at most every 10 minutes: the dashboard and the agent ask far more often than backups change."""
    if time.monotonic() - _backup_cache["at"] < 600 and _backup_cache["value"] is not None:
        return _backup_cache["value"]
    out = []
    for kind, pattern in BACKUP_KINDS.items():
        try:
            newest = max(BACKUP_DIR.glob(pattern), key=lambda p: p.name, default=None)
            path = BACKUP_DIR / f"status-{kind}.json"
            status = json.loads(path.read_text()) if path.exists() else {}
            at = datetime.fromtimestamp(newest.stat().st_mtime, UTC) if newest else None
        except (OSError, ValueError):
            newest, status, at = None, {}, None
        out.append({"kind": kind, "newest": newest.name if newest else None, "at": at, "status": status})
    _backup_cache.update(at=time.monotonic(), value=out)
    return out


def _ago(at: datetime | None, now: datetime) -> str:
    if not at:
        return "nunca"
    minutes = int((now - at).total_seconds() // 60)
    return f"há {minutes} min" if minutes < 120 else f"há {minutes // 60} h" if minutes < 48 * 60 else f"há {minutes // 1440} dias"


def _backup_component(b: dict[str, Any], now: datetime) -> dict[str, Any]:
    id_ = f"backup_{b['kind']}"
    name = {"db": "Cópia da base de dados", "vault": "Cópia do cofre (Obsidian)", "repo": "Cópia do repositório"}[b["kind"]]
    s = b["status"]
    if not b["newest"]:
        return _c(id_, name, "warn" if b["kind"] == "repo" else "error", "Nenhuma cópia encontrada.", "backup_missing")
    age = f"Última: {b['newest']} ({_ago(b['at'], now)})."
    if s and not s.get("ok"):
        return _c(id_, name, "error", f"A última execução falhou: {s.get('error') or '?'}. {age}", "backup_failed")
    if now - b["at"] > BACKUP_LATE:
        return _c(id_, name, "error", f"Atrasada. {age}", "backup_stale")
    notes = []
    if s.get("restore_test") == "passed":
        notes.append("reposição testada")
    if s.get("offsite") == "verified":
        notes.append("cópia fora do disco verificada" + (" (cifrada)" if s.get("encrypted") else " (não cifrada)"))
    elif s:
        notes.append("só neste disco")
    code = None
    if s and s.get("offsite") != "verified":
        code = "backup_local_only"
    elif s and not s.get("encrypted"):
        code = "backup_unencrypted"
    return _c(id_, name, "warn" if code else "ok", age + (" " + ", ".join(notes) + "." if notes else ""), code)


def check_backups(session: Session, now: datetime, tz: ZoneInfo) -> bool:
    """An alert (once a day) when the database backup failed or is late: a silent backup failure is found too late."""
    db = next((b for b in backups(now) if b["kind"] == "db"), None)
    if not db or (not db["newest"] and not db["status"]):
        return False  # backups were never set up here (e.g. a Docker install without the backup folder)
    c = _backup_component(db, now)
    if c["state"] != "error":
        return False
    return _add(session, key=f"backup:{now.astimezone(tz):%Y-%m-%d}", kind="integration", title="Cópia de segurança com problemas",
                body=c["detail"][:300], url="/definicoes", actions=["open"], urgent=False, deliver_after=now,
                expires_at=now + timedelta(hours=12))


def report(session: Session, now: datetime) -> dict[str, Any]:
    from peace import assistant, mail  # both import routes; importing them at module level would cycle

    comps = [_c("database", "Base de dados", "ok", "A responder.")]
    worker = session.get(Preference, "worker")
    seen = datetime.fromisoformat(worker.value["seen_at"]) if worker else None
    alive = bool(seen and now - seen < WORKER_SILENT)
    comps.append(_c("worker", "Serviço de fundo", "off" if not seen else "ok" if alive else "error",
                    "Não corre nesta instalação." if not seen else f"Último sinal {_ago(seen, now)}.",
                    "worker_silent" if seen and not alive else None))
    for s in sync.status(session, now, sync.next_slot(now, routes._tz())):
        bad = s["last_status"] in ("failed", "auth_failed")
        comps.append(_c(f"sync_{s['source']}", f"Sincronização {s['source']}", "error" if bad else "warn" if s["stale"] else "ok",
                        f"Último sucesso {_ago(s['last_success_at'], now)}." + (" Precisa de nova palavra-passe." if s["needs_login"] else ""),
                        "sync_auth" if s["needs_login"] else "sync_failed" if bad else "sync_stale" if s["stale"] else None))
    login, box = sync.mail_login(session), mail.state(session)
    if login["enabled"]:
        comps.append(_c("mail", "Email da universidade",
                        "error" if box.get("refused") or box.get("ok") is False else "ok" if box.get("ok") else "warn",
                        "Precisa de nova palavra-passe." if box.get("refused") else box.get("error") or
                        (f"Lido {_ago(datetime.fromisoformat(box['last_success']), now)}." if box.get("last_success") else "Ainda não foi lido."),
                        "sync_auth" if box.get("refused") else "sync_failed" if box.get("ok") is False else None))
    st = assistant.status(session, now)
    m = st["model"]
    gpu, loaded = llm.gpu_memory(), llm.loaded_models()
    mem = f" Placa gráfica: {gpu['used'] // 2**20} de {gpu['total'] // 2**20} MiB em uso." if gpu else ""
    in_memory = any(x["name"] == m["name"] for x in loaded)
    detail = (("Em uso (carregado)." if in_memory else "Em uso (carrega no próximo pedido).") if m["reachable"] else
              f"Em pausa: {m['paused']}. A PEACE usa as suas regras." if m.get("paused") else "Indisponível: a PEACE usa as suas regras.")
    comps.append(_c("model", f"Modelo local ({m['name']})", "ok" if m["reachable"] else "warn", detail + mem,
                    None if m["reachable"] else "model_paused" if m.get("paused") else "model_unavailable",
                    loaded=[x["name"] for x in loaded]))
    d, audio = st["desktop"], st["voice"]["audio"] or {}
    comps.append(_c("desktop", "Agente do PC", "ok" if d["connected"] else "off",
                    f"Ligado (último sinal {_ago(d['seen_at'], now)})." if d["connected"] else "Desligado (na instalação de desenvolvimento é de propósito).",
                    None if d["connected"] else "agent_offline"))
    if d["connected"]:
        browser = (d["actions"] or {}).get("browser")
        comps.append(_c("browser", "Brave (separadores)", "ok" if browser else "warn",
                        "Extensão ligada." if browser else "A extensão não está ligada: recarrega-a em brave://extensions.",
                        None if browser else "browser_off"))
        voice_ok, gpu_stt = st["voice"]["available"], audio.get("stt_gpu")
        engine = "placa gráfica" if gpu_stt else "CPU (o servidor da placa gráfica não responde)" if audio.get("stt_available") else "nenhum"
        lat = audio.get("latency") or {}
        last = f" Última volta: reconhecimento {lat['stt_ms']} ms, resposta {lat.get('agent_ms', '?')} ms." if lat.get("stt_ms") else ""
        comps.append(_c("voice", "Voz (SUPER+Y)", "ok" if voice_ok and gpu_stt is not False else "warn" if voice_ok else "error",
                        f"Reconhecimento: {engine}.{last}" + (f" Erro: {audio['error']}" if audio.get("error") else ""),
                        None if voice_ok and gpu_stt is not False else "stt_cpu_fallback" if voice_ok else "voice_unavailable"))
    comps += [_backup_component(b, now) for b in backups(now)]
    job = assistant.desktop._live(assistant.desktop._pref(session, "desktop_job"), now)
    history = session.scalars(select(AuditEvent).where(AuditEvent.action.like("desktop.%") | AuditEvent.action.like("assistant.%"))
                              .order_by(AuditEvent.id.desc()).limit(15)).all()
    return {
        "checked_at": now,
        "components": comps,
        "last_job": assistant.desktop.public(job) if job else None,
        "history": [{"at": h.created_at, "action": h.action, "detail": {k: v for k, v in h.detail.items() if k != "title"}} for h in history],
    }


@router.get("/diagnostics")
def diagnostics(session: Session = Depends(get_session), now: datetime = Depends(routes.get_now)):
    return report(session, now)

"""API for the Playwright suite: fresh *_test database, deterministic seed, built dashboard on :8431."""

import os
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PEACE_DATABASE_URL", "postgresql+psycopg://peace@127.0.0.1:55432/peace_e2e_test")
os.environ.setdefault("MOODLE_URL", "https://moodle.example.pt/moodle")
os.environ.setdefault("INFORESTUDANTE_URL", "https://inforestudante.example.pt/nonio")
os.environ.setdefault("PEACE_API_TOKEN", "e2e-token-" + "x" * 32)
os.environ["PEACE_DASHBOARD_DIR"] = str(ROOT / "dashboard" / "dist")
os.environ["PEACE_LLM_URL"] = "http://127.0.0.1:9"  # no model in tests: the built-in commands answer, deterministically

import psycopg  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from peace.api import app  # noqa: E402
from peace.config import database_url  # noqa: E402
from peace.db import get_engine  # noqa: E402
from peace.library import term_frequencies  # noqa: E402
from peace.models import Change, Course, Event, Material, SyncRun, Task  # noqa: E402

url = database_url()
assert (url.database or "").endswith("_test"), "the e2e server wipes its database; use a *_test one"
try:
    psycopg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False)).close()
except psycopg.OperationalError:
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{url.database}"')

with get_engine().begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

now = datetime.now(UTC)
tz = ZoneInfo("Europe/Lisbon")
local = now.astimezone(tz)
end_of_today = datetime.combine(local.date(), time(23, 30), tz)
with Session(get_engine()) as s:
    redes = Course(name="Redes de Computadores", code="1001", class_groups="P1, T1", source="inforestudante", external_id="1001")
    bd = Course(name="Bases de Dados", code="1002", source="inforestudante", external_id="1002",
                url="https://moodle.example.pt/moodle/course/view.php?id=1")
    s.add_all([redes, bd])
    s.flush()
    s.add_all([
        Task(title="Relatório de sockets", course_id=redes.id, priority=3, due_at=now - timedelta(hours=2), estimated_minutes=90),
        Task(title="Entregar ficha 3", course_id=bd.id, due_at=min(now + timedelta(hours=2), max(end_of_today, now + timedelta(minutes=30))), estimated_minutes=30),
        Task(title="Projeto final de BD", course_id=bd.id, kind="assignment", due_at=now + timedelta(days=5), estimated_minutes=300,
             source="moodle", external_id="assign:1", url="https://moodle.example.pt/moodle/mod/assign/view.php?id=1"),
        Task(title="Ler artigo sobre TCP", course_id=redes.id, priority=1),
        Event(title="RC (P1)", kind="class", course_id=redes.id, starts_at=now + timedelta(hours=1), ends_at=now + timedelta(hours=2),
              location="L2.1", source="inforestudante", external_id="aulas:1"),
        Event(title="BD (Teste 1)", kind="exam", course_id=bd.id, starts_at=now + timedelta(days=2), ends_at=now + timedelta(days=2, hours=2),
              source="inforestudante", external_id="avaliacoes:1"),
        Material(course_id=redes.id, section="Teóricas", title="Slides: Introdução", kind="file", filename="1-intro.pdf",
                 size=2_400_000, modified_at=now - timedelta(days=2), source="moodle", external_id="file:1:/1-intro.pdf",
                 vault_path="02 - University/Courses/Redes de Computadores/Materiais/Teóricas/1-intro.pdf",
                 terms=term_frequencies("Sockets TCP e UDP. Um socket liga dois processos. Relatório: sockets UDP, sockets TCP.")),
        Change(source="moodle", entity="task", kind="created", summary="Novo trabalho: Projeto final de BD"),
        SyncRun(source="inforestudante", trigger="scheduled", status="success", started_at=now - timedelta(hours=1), finished_at=now - timedelta(hours=1)),
        SyncRun(source="moodle", trigger="scheduled", status="success", started_at=now - timedelta(hours=1), finished_at=now - timedelta(hours=1)),
    ])
    s.commit()

uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PEACE_E2E_PORT", "8431")), log_level="warning")

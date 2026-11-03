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
os.environ.setdefault("PEACE_ACADEMIC_DOMAINS", "escola.example.pt")
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
from peace.models import Change, Course, Event, Material, Proposal, StudySession, SyncRun, Task  # noqa: E402

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
    s.flush()
    # A deadline moved earlier left one session after it: a pending proposal for the Rever page.
    project = s.query(Task).filter_by(external_id="assign:1").one()
    later = datetime.combine((now + timedelta(days=6)).astimezone(tz).date(), time(10), tz)
    s.add(StudySession(task_id=project.id, course_id=bd.id, starts_at=later, ends_at=later + timedelta(hours=1), reason="plano antigo"))
    s.add(Proposal(key="change:e2e", kind="replan", title="Re-planear «Projeto final de BD»", task_id=project.id,
                   reason="Prazo antecipado 2 dias.", evidence=[{"source": "moodle", "ref": "change:e2e", "rule": "deadline_earlier",
                                                                 "quote": "Prazo alterado: Projeto final de BD"}]))
    # A second one, for the careless-user tests (e2e/careless.spec.ts): an exam moved before its prep session.
    exam = s.query(Event).filter_by(external_id="avaliacoes:1").one()
    after = exam.starts_at + timedelta(days=1)
    s.add(StudySession(event_id=exam.id, course_id=bd.id, starts_at=after, ends_at=after + timedelta(hours=1), reason="preparação antiga"))
    s.add(Proposal(key="change:e2e-exam", kind="replan", title="Re-planear «BD (Teste 1)»", event_id=exam.id,
                   reason="Avaliação antecipada 2 dias.", evidence=[{"source": "inforestudante", "ref": "change:e2e-exam",
                                                                     "rule": "assessment_moved", "quote": "Data alterada: BD (Teste 1)"}]))
    s.commit()

# Email (phase 4): an in-memory Exchange mailbox (the backend tests' fake), read once through the real code.
import sys  # noqa: E402
from email.utils import format_datetime  # noqa: E402

from peace import mail  # noqa: E402
from peace.models import Preference  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from test_mail import TRASH, FakeBox  # noqa: E402

box = FakeBox()
mail.connect = lambda login: box


def raw(sender, subject, days, extra="", body="Olá."):
    return (f"From: {sender}\r\nSubject: {subject}\r\nDate: {format_datetime(now - timedelta(days=days))}\r\n{extra}"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n").encode()


box.add("INBOX", raw("Prof. Rui <rui@escola.example.pt>", "BD: enunciado do projeto final", 3, body="O projeto final entrega-se a 20 de novembro."), 3, ["\\Seen"])
box.add("INBOX", raw("Loja <news@loja.pt>", "Promoções de outono", 45, "List-Unsubscribe: <mailto:u@loja.pt>\r\n"), 45, ["\\Seen"])
box.add("INBOX", raw("Moodle <noreply@moodle.example.pt>", "Novo aviso no fórum", 40), 40, ["\\Seen"])
box.add(TRASH, raw("a@b.pt", "Apagado por mim", 1), 1)
with Session(get_engine()) as s:
    s.add(Preference(key="ui", value={"language": "pt", "features": dict.fromkeys(("holo", "graph", "mail", "mastery"), True), "dock": [
        "/", "/calendario", "/estudo", "/tarefas", "/conhecimento", "/ficheiros", "/prazos", "/cadeiras", "/definicoes",
        "tool:all", "tool:new", "tool:sync", "tool:holo", "tool:shortcuts", "tool:hide"]}))  # most tests walk every page; simplify.spec.ts covers the defaults
    s.add(Preference(key="integrations", value={"email": {"enabled": True, "url": "https://webmail.example.pt",
                                                          "username": "aluno", "password": "e2e", "clean_junk": True, "quota_mb": 100}}))
    s.commit()
    mail.run(s, now)
    mail.offer_cleanup(s, now)
    # Phase 4b: what the model would have found in the teacher's email (the model itself is not used in tests).
    from peace import mail_extract
    from peace.models import MailMessage
    teacher = s.query(MailMessage).filter(MailMessage.sender == "rui@escola.example.pt").one()
    project = s.query(Task).filter_by(external_id="assign:1").one()
    later = datetime.combine((project.due_at + timedelta(days=2)).astimezone(tz).date(), time(23, 59), tz)
    mail_extract.decide(s, teacher, {"kind": "deadline", "title": "Projeto final de BD", "at": later.isoformat(), "timed": True,
                                     "date_text": "dia 20 de novembro", "course_id": project.course_id,
                                     "quote": "O projeto final entrega-se a 20 de novembro."}, now)
    s.commit()

# Phase 5: a brief for "Projeto final de BD", the model's answer for it, and a project folder in a throwaway home.
import tempfile  # noqa: E402

from peace import requirements  # noqa: E402

home = Path(tempfile.mkdtemp(prefix="peace-e2e-home-"))
os.environ["HOME"] = str(home)  # the project folder must be inside the student's home
(home / "projeto-bd").mkdir()
(home / "projeto-bd" / "main.py").write_text("print('ok')\n")
BRIEF = ("[p. 1] Projeto final de BD\n\nO programa deve validar o NIF de cada cliente.\n\n"
         "[p. 2] A entrega é um ficheiro ZIP com o relatório em PDF.\n\nNão é permitido usar ORMs.")
with Session(get_engine()) as s:
    bd = s.query(Course).filter_by(code="1002").one()
    s.add(Material(course_id=bd.id, section="Projeto", title="Enunciado do projeto", kind="file", filename="Projeto_BD.pdf",
                   content=BRIEF, modified_at=now - timedelta(days=3), source="moodle", external_id="file:projeto",
                   terms=term_frequencies("projeto final projeto final BD NIF cliente entrega relatório")))
    s.commit()
requirements.llm_reachable = lambda: True
requirements.ask_json = lambda system, user, schema, timeout=180: {"items": [
    {"kind": "functional", "text": "Validar o NIF de cada cliente", "quote": "O programa deve validar o NIF de cada cliente."},
    {"kind": "deliverable", "text": "Entregar ZIP com relatório em PDF", "quote": "A entrega é um ficheiro ZIP com o relatório em PDF."},
    {"kind": "constraint", "text": "Não usar ORMs", "quote": "Não é permitido usar ORMs."},
]} if "Projeto final de BD" in user else {"items": []}

# Phase 6: the topics the model would have read in BD's files, and its concept proposal for them.
from peace import mastery  # noqa: E402

with Session(get_engine()) as s:
    for m in s.query(Material).filter(Material.course_id == s.query(Course).filter_by(code="1002").one().id):
        m.topics, m.doc_kind = ["modelo entidade-relação", "normalização", "chaves primárias"], "slides"
    s.commit()
mastery.llm_reachable = lambda: True
mastery.ask_json = lambda system, user, schema, timeout=180: {"concepts": [
    {"name": "Modelo E/R", "description": "Entidades, relações e cardinalidade.", "topics": ["modelo entidade-relação"], "requires": []},
    {"name": "Chaves", "description": "Identificar cada linha.", "topics": ["chaves primárias"], "requires": ["Modelo E/R"]},
    {"name": "Normalização", "description": "Formas normais.", "topics": ["normalização"], "requires": ["Chaves"]},
]}

# Phase 7: the image reader returns what it really read from a student's photo; the text model classifies and
# writes questions whose quote is in BD's brief; the blind answer agrees with the first question only.
import json  # noqa: E402

from peace import exams, practice  # noqa: E402

# Real exam text, so not in the public copy; the exam browser test is skipped there.
_READS = Path(__file__).parents[1] / "tests" / "fixtures" / "past_exam_reads.json"
PHOTO = json.loads(_READS.read_text())["BD-sql-foto-torta"] if _READS.exists() else []
_halves = iter(PHOTO * 1000)
exams.vision_unavailable = lambda: None
exams.llm_reachable = lambda: True
exams.see_json = lambda prompt, image, schema: next(_halves)
exams.ask_json = lambda system, user, schema, timeout=180: {
    "kind": "sql_ddl" if "Crie a tabela" in user else "sql_query", "asks": "Escrever um comando SQL.", "concept": ""}
from peace.models import ExamQuestion  # noqa: E402

with Session(get_engine()) as s:  # a programming question as generation keeps them: its solution passed in the Sandbox
    setup = "CREATE TABLE aluno(nome TEXT, nota INT);\nINSERT INTO aluno VALUES ('Ana', 15), ('Rui', 8), ('Eva', 12);"
    s.add(ExamQuestion(course_id=s.scalar(text("SELECT id FROM courses WHERE name = 'Bases de Dados'")), qtype="code", kind="sql_query",
                       status="ok", text="Escreve uma consulta com o nome dos alunos com nota igual ou superior a 10.", printed=setup,
                       answer={"language": "sql", "solution": "SELECT nome FROM aluno WHERE nota >= 10", "tests": setup},
                       explanation="WHERE filtra as linhas.", provenance={"source": "generated"}))
    s.commit()
RUBRIC = {"criteria": [{"criterion": "Escreve o comando pedido", "points": 6}, {"criterion": "Justifica", "points": 4}]}
MARKS = {"marks": [{"points": 5, "comment": "Quase completo."}, {"points": 1, "comment": "Falta justificar."}], "feedback": "Justifica as escolhas."}
practice.llm_reachable = lambda: True
practice.ask_json = lambda system, user, schema, timeout=180: RUBRIC if "criteria" in schema["properties"] else \
    MARKS if "marks" in schema["properties"] else {"questions": [
    {"qtype": "mcq", "text": "O que deve o programa validar em cada cliente?", "options": ["A morada", "O NIF", "O email", "O telefone"],
     "correct": 1, "explanation": "O enunciado pede a validação do NIF.", "quote": "O programa deve validar o NIF de cada cliente.",
     "difficulty": "easy", "kind": "theory"},
    {"qtype": "mcq", "text": "Em que formato se entrega o relatório do projeto?", "options": ["DOCX", "TXT", "PDF", "ODT"],
     "correct": 2, "explanation": "Relatório em PDF.", "quote": "A entrega é um ficheiro ZIP com o relatório em PDF.",
     "difficulty": "easy", "kind": "theory"},
]} if "questions" in schema["properties"] else {"choice": 1, "ambiguous": False}

uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PEACE_E2E_PORT", "8431")), log_level="warning")

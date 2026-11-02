"""Questions about one of the student's own tasks or events, and course acronyms (RC, AM2...)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from peace import assistant
from peace.models import Course, Event, Task

NOW = datetime(2026, 10, 28, 20, 0, tzinfo=UTC)


def courses(session):
    session.add_all([Course(name="Introdução às Redes de Comunicação", code="60023100"),
                     Course(name="Análise Matemática I", code="60022849"), Course(name="Análise Matemática II", code="60023055"),
                     Course(name="Base de Dados", code="61000608"), Course(name="Sistemas Operativos", code="60023747")])
    session.commit()


def ask(session, text):
    return assistant.ask(session, text, "text", NOW)


def test_short_and_numbered_acronyms(session):
    courses(session)
    by = {a: c.name for a, c in assistant.course_acronyms(session).items()}
    assert by["irc"] == by["rc"] == "Introdução às Redes de Comunicação"
    assert by["am1"] == "Análise Matemática I" and by["am2"] == "Análise Matemática II" and by["bd"] == "Base de Dados"
    assert assistant._course_named(session, "Relatório de RC").name == "Introdução às Redes de Comunicação"
    assert assistant.find_course(session, "AM2").name == "Análise Matemática II"


def test_the_rc_report_question(session):
    courses(session)
    session.add(Task(title="Relatório de RC", due_at=datetime(2026, 10, 30, 18, 0, tzinfo=UTC), created_at=NOW - timedelta(days=1)))
    session.commit()
    reply = ask(session, "o que e o relatorio de rc e de que cadeiras e")
    assert reply["intent"] == "item_info"
    assert "«Relatório de RC» é uma tarefa de Introdução às Redes de Comunicação" in reply["reply"]
    assert "sexta" in reply["reply"] and "Criaste-a tu" in reply["reply"] and "já a liguei" in reply["reply"]
    session.expire_all()
    assert session.scalar(select(Task)).course.code == "60023100"
    assert "já a liguei" not in ask(session, "de que cadeira é o relatório de RC?")["reply"]


def test_imported_items_say_where_they_come_from(session):
    courses(session)
    so = session.scalar(select(Course).filter_by(code="60023747"))
    session.add(Task(title="Meta 2", course_id=so.id, source="moodle", external_id="1", description="<p>Entregar o <b>shell</b>.</p>",
                     url="https://moodle.example/meta2", due_at=NOW + timedelta(days=3)))
    session.add(Event(title="Teste SO", kind="exam", course_id=so.id, starts_at=NOW + timedelta(days=5), ends_at=NOW + timedelta(days=5, hours=2),
                      location="A1", source="inforestudante", external_id="x"))
    session.commit()
    meta = ask(session, "o que é a meta 2?")
    assert "tarefa de Sistemas Operativos" in meta["reply"] and "Vem do Moodle" in meta["reply"] and "Diz: Entregar o shell ." in meta["reply"]
    exam = ask(session, "quando é o teste SO?")
    assert exam["intent"] == "item_info" and "uma avaliação de Sistemas Operativos" in exam["reply"] and "em A1" in exam["reply"]


def test_subject_questions_still_go_to_the_materials(session):
    session.add(Task(title="Relatório de sockets"))
    session.commit()
    assert assistant._asked_about(session, NOW, "o que é um socket?") is None


def test_a_double_send_creates_one_task(session):
    one = assistant.cmd_add_task(session, NOW, text="adiciona uma tarefa: relatório de RC até sexta às 18h")
    session.commit()
    two = assistant.cmd_add_task(session, NOW, text="adiciona uma tarefa: relatório de RC até sexta às 18h")
    session.commit()
    assert one.text.startswith("Criei") and two.text.startswith("Já tinha criado")
    assert len(session.scalars(select(Task)).all()) == 1


def test_exact_copies_are_one_item_with_a_warning(session):
    courses(session)
    due = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)
    session.add_all([Task(title="Relatório de RC", due_at=due), Task(title="Relatório de RC", due_at=due)])
    session.commit()
    reply = ask(session, "o que é o relatório de RC?")["reply"]
    assert reply.startswith("«Relatório de RC» é uma tarefa de Introdução às Redes de Comunicação") and "2 cópias iguais" in reply

"""Event type from a Google Calendar title: pt-PT rules, course match, then the local model, then appointment."""

import pytest

from peace.classify import classify

COURSES = [(1, "AM2", "Análise Matemática II"), (2, "SO", "Sistemas Operativos"), (3, None, "Redes de Comunicação")]


@pytest.mark.parametrize("title, kind, course", [
    ("Exame de Análise Matemática II", "exam", 1),
    ("Teste SO", "exam", 2),
    ("Mini-teste 2 de Redes de Comunicação", "exam", 3),
    ("Frequência AM2", "exam", 1),
    ("Recurso - Sistemas Operativos", "exam", 2),
    ("Época especial", "exam", None),
    ("Defesa do projeto", "exam", None),
    ("Aula SO", "class", 2),
    ("TP3 Análise Matemática II", "class", 1),
    ("PL2", "class", None),
    ("Teórica de redes de comunicacao", "class", 3),
    ("AM2", "class", 1),  # a course alone in a calendar is almost always its class
    ("Dentista", "appointment", None),
    ("Jantar com a Rita", "appointment", None),
    ("Ginásio", "appointment", None),
    ("Reunião com o orientador", "appointment", None),
])
def test_rules(title, kind, course):
    assert classify(title, "", COURSES) == (kind, course)


def test_description_counts_too():
    assert classify("Sala 2.1", "Teste intercalar de SO", COURSES) == ("exam", 2)


def test_words_inside_other_words_do_not_match():
    assert classify("Protesto na praça", "", COURSES) == ("appointment", None)  # "teste" inside? no: "testo"
    assert classify("Aulas de condução", "", COURSES)[0] == "class"
    assert classify("Soalho novo", "", COURSES) == ("appointment", None)  # "SO" only as a whole word
    assert classify("Jantar só eu", "", COURSES) == ("appointment", None)  # nor the word "só"


def test_model_decides_only_when_rules_cannot():
    asked = []

    def model(title, description):
        asked.append(title)
        return "exam"
    assert classify("Apresentação final", "", COURSES, model) == ("exam", None)
    assert classify("Exame SO", "", COURSES, model) == ("exam", 2) and asked == ["Apresentação final"]


def test_model_unavailable_or_off_script_falls_back_to_appointment():
    assert classify("Apresentação final", "", COURSES, lambda t, d: None) == ("appointment", None)
    assert classify("Apresentação final", "", COURSES, lambda t, d: "party") == ("appointment", None)

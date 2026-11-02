"""Questions about the material: answered only from the course files, with the source."""

from peace import answers
from peace.models import Course, Material


class Reply:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": self.content}}


def files(session):
    moad = Course(name="Métodos de Otimização e Análise de Dados", code="MOAD")
    session.add(moad)
    session.flush()
    session.add_all([
        Material(course_id=moad.id, section="T", title="Capítulo 2", kind="file", filename="cap2.pdf", source="moodle",
                 external_id="1", content="[p. 1] Introdução à programação linear. [p. 2] " + "texto " * 300
                 + "[p. 3] O método simplex percorre os vértices da região admissível até encontrar a solução ótima."),
        Material(course_id=moad.id, section="T", title="Notas", kind="file", filename="notas.txt", source="moodle",
                 external_id="2", content="Exercícios de grafos e caminhos mais curtos."),
    ])
    session.commit()
    return moad


def test_the_best_excerpt_and_its_page(session):
    files(session)
    found = answers.search(session, "como funciona o método simplex?")
    assert found[0].material.title == "Capítulo 2" and found[0].page == "2–3" and "vértices" in found[0].text


def test_only_cited_answers_from_the_excerpts_are_shown(session, monkeypatch):
    moad = files(session)
    seen = {}

    def llm(method, path, **kw):
        seen["prompt"] = kw["json"]["messages"][1]["content"]
        return Reply("O **simplex** percorre os vértices da região admissível [1].")

    monkeypatch.setattr(answers, "_llm", llm)
    text, sources = answers.answer(session, "o que é o simplex?", moad, use_model=True)
    assert text == "O simplex percorre os vértices da região admissível [1]." and sources[0]["detail"].endswith("p. 2–3")
    assert "vértices" in seen["prompt"]
    monkeypatch.setattr(answers, "_llm", lambda *a, **k: Reply("É um algoritmo de 1947 de Dantzig."))  # no citation
    text, _ = answers.answer(session, "o que é o simplex?", moad, use_model=True)
    assert text.startswith("Encontrei isto nos materiais: [1] Capítulo 2")
    assert answers.answer(session, "receita de bacalhau", None, use_model=False) == (answers.NOT_FOUND, [])
    monkeypatch.setattr(answers, "_llm", lambda *a, **k: Reply(answers.NOT_FOUND))
    text, sources = answers.answer(session, "simplex dual", moad, use_model=True)
    assert text.startswith(answers.NOT_FOUND + " O mais próximo que encontrei: [1] Capítulo 2") and sources
    monkeypatch.setattr(answers, "_llm", lambda *a, **k: Reply(r"Na linha \(z_j-c_j\) [1]."))
    assert answers.answer(session, "simplex", moad, use_model=True)[0] == "Na linha z_j-c_j [1]."

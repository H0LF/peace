"""Parsers for inforestudante and Moodle, on synthetic samples shaped like the real responses."""

from datetime import UTC, datetime, timedelta

from peace.sources import inforestudante as ir
from peace.sources import moodle as md

COURSES_PAGE = """
<table class="displaytable"><thead><tr><th>Nome</th><th>Código</th><th>Turmas</th><th>Regime</th><th></th></tr></thead>
<tr class="lightrow"><td class="contentLeft">Álgebra Linear</td><td>60000002</td><td></td><td>1.º Semestre</td>
<td><a href="init.do?args=1">Detalhes</a></td></tr>
<tr class="darkrow"><td class="contentLeft">Análise Matemática I</td><td>60000001</td>
<td><label title="Práticas">P4</label>, <label title="Teóricas">T2</label></td><td>1.º Semestre</td><td></td></tr>
</table>"""

TIMETABLE = [
    {"id": "1057867", "className": "aulas", "title": "AM1 (P4) - S1.01", "start": "2026-11-16 12:00",
     "end": "2026-11-16 13:00", "description": "Análise Matemática I (60000001)"},
    {"id": "197343", "className": "avaliacoes", "title": "BD (Teste 1) - A1, S1.1", "start": "2026-10-28 16:00",
     "end": "2026-10-28 17:30", "description": "Base de Dados (61000001)"},
    {"id": "9", "className": "defesas", "title": "X", "start": "2026-11-01", "end": None, "description": ""},
]


def test_inforestudante_courses_with_groups():
    assert ir.parse_courses(COURSES_PAGE) == [
        {"code": "60000002", "name": "Álgebra Linear", "groups": []},
        {"code": "60000001", "name": "Análise Matemática I", "groups": ["P4", "T2"]},
    ]


def test_inforestudante_timetable_local_times_rooms_and_codes():
    events, skipped = ir.parse_timetable(TIMETABLE)
    cls, exam = events
    assert cls == {
        "external_id": "aulas:1057867", "kind": "class", "title": "AM1 (P4)",
        "starts_at": datetime(2026, 11, 16, 12, tzinfo=ir.LISBON), "ends_at": datetime(2026, 11, 16, 13, tzinfo=ir.LISBON),
        "location": "S1.01", "course_code": "60000001",
    }
    # 28 Oct 2026 is after the DST switch: 16:00 Lisbon == 16:00 UTC.
    assert (exam["kind"], exam["title"], exam["starts_at"].astimezone(UTC).hour) == ("exam", "BD (Teste 1)", 16)
    assert skipped == {"defesas": 1}


def test_scraped_html_becomes_plain_text():
    events, _ = ir.parse_timetable([{**TIMETABLE[0], "title": "<b>AM1</b> <script>x</script>"}])
    assert "<" not in events[0]["title"]


NOW = datetime(2026, 10, 26, 13, 0, tzinfo=UTC)


def ts(dt):
    return int(dt.timestamp())


def test_moodle_courses_code_prefix_and_year_prefix():
    raw = [
        {"id": 1, "fullname": "60000001 - Análise Matemática I", "visible": 1},
        {"id": 2, "fullname": "2026- Base de Dados", "visible": 1},
        {"id": 3, "fullname": "Escondida", "visible": 0},
    ]
    assert [(c["code"], c["name"]) for c in md.parse_courses(raw)] == [("60000001", "Análise Matemática I"), (None, "Base de Dados")]


def test_moodle_assignment_window_and_submission_status():
    raw = {"courses": [{"id": 7, "assignments": [
        {"id": 1, "cmid": 11, "name": "Lab 04", "intro": "<p>Texto</p>", "duedate": ts(NOW + timedelta(days=7))},
        {"id": 2, "cmid": 12, "name": "Projeto", "intro": "", "duedate": ts(NOW - timedelta(days=15))},  # too old
        {"id": 3, "cmid": 13, "name": "Justificação de faltas", "intro": "", "duedate": 0},  # no deadline
        {"id": 4, "cmid": 14, "name": "Entregue", "intro": "", "duedate": ts(NOW - timedelta(days=3))},
    ]}]}
    window = md.assignments_in_window(raw, NOW)
    assert [a["id"] for a in window] == [1, 4]
    statuses = {4: {"lastattempt": {"submission": {"status": "submitted", "timemodified": ts(NOW - timedelta(days=4))}}},
                1: {"lastattempt": {"teamsubmission": {"status": "new"}}}}
    tasks = md.parse_assignments(window, statuses)
    assert tasks[0] | {} == {
        "external_id": "assign:1", "moodle_course": 7, "title": "Lab 04", "description": "Texto",
        "due_at": datetime.fromtimestamp(ts(NOW + timedelta(days=7)), UTC),
        "url": "https://moodle.example.pt/moodle/mod/assign/view.php?id=11", "submitted_at": None,
    }
    assert tasks[1]["submitted_at"] == datetime.fromtimestamp(ts(NOW - timedelta(days=4)), UTC)


def test_moodle_other_activities_and_foreign_urls_are_dropped():
    raw = {"events": [
        {"modulename": "assign", "instance": 1, "name": "a", "timesort": 1, "course": {"id": 7}},
        {"modulename": "quiz", "instance": 5, "name": "Quiz 2 closes", "activityname": "Quiz 2", "timesort": ts(NOW),
         "course": {"id": 7}, "url": "https://evil.example/phish"},
    ]}
    (quiz,) = md.parse_action_events(raw)
    assert (quiz["external_id"], quiz["title"], quiz["url"]) == ("quiz:5", "Quiz 2", None)


def group_copies():
    due = NOW + timedelta(days=14)
    return [
        {"external_id": f"assign:{n}", "moodle_course": 7, "title": f"Meta3 - Modelos ER na P{n}  - pdf",
         "due_at": due, "url": f"u{n}", "submitted_at": None, "description": ""}
        for n in (1, 2, 3)
    ] + [{"external_id": "assign:9", "moodle_course": 7, "title": "Relatório P1", "due_at": NOW,
          "url": "u9", "submitted_at": None, "description": ""}]


def test_group_copies_keep_the_students_own_group():
    kept = md.collapse_group_copies(group_copies(), {7: {"P2", "T1"}})
    assert sorted(t["external_id"] for t in kept) == ["assign:2", "assign:9"]


def test_group_copies_merge_when_group_unknown_and_singletons_untouched():
    kept = md.collapse_group_copies(group_copies(), {})
    merged = next(t for t in kept if t["external_id"] == "assign:1")
    assert merged["title"] == "Meta3 - Modelos ER"
    assert merged["url"] == "https://moodle.example.pt/moodle/course/view.php?id=7"
    assert next(t for t in kept if t["external_id"] == "assign:9")["title"] == "Relatório P1"
    assert len(kept) == 2

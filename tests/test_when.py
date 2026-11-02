from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from peace.when import parse_when

LX = ZoneInfo("Europe/Lisbon")
NOW = datetime(2026, 10, 27, 13, 0, tzinfo=UTC)  # Tuesday 13:00 in Lisbon


@pytest.mark.parametrize("text, at, rest", [
    ("entregar o relatório de RC até sexta às 23h59", "2026-10-30 23:59", "entregar o relatório de RC"),
    ("ligar à Ana amanhã às 15h", "2026-10-28 15:00", "ligar à Ana"),
    ("estudar BD daqui a 2 horas", "2026-10-27 15:00", "estudar BD"),
    ("ligar à Ana daqui a 2h", "2026-10-27 15:00", "ligar à Ana"),  # how Whisper writes "duas horas"
    ("reunião às 8h da noite de amanhã", "2026-10-28 20:00", "reunião"),
    ("reunião amanhã às oito da noite", "2026-10-28 20:00", "reunião"),
    ("dentista na sexta às dez e meia", "2026-10-30 10:30", "dentista"),
    ("comprar as duas pizzas às 9 da noite", "2026-10-27 21:00", "comprar as duas pizzas"),
    ("teste de MOAD dia 3", "2026-11-03 09:00", "teste de MOAD"),  # day 3 has passed in October
    ("projeto de SO 3 de novembro às 18:30", "2026-11-03 18:30", "projeto de SO"),
    ("trabalho 2 de AM até 31/10", "2026-10-31 09:00", "trabalho 2 de AM"),
    ("Lab 04 na próxima segunda às 10h", "2026-11-02 10:00", "Lab 04"),
    ("enviar email hoje às 3 da tarde", "2026-10-27 15:00", "enviar email"),
    ("comprar café às 9h", "2026-10-28 09:00", "comprar café"),  # 9:00 has passed today
    ("buscar a Ana às 5", "2026-10-27 17:00", "buscar a Ana"),  # a bare "às 5" is the afternoon
    ("reunião ao meio-dia", "2026-10-28 12:00", "reunião"),
    ("Trabalho 2 da cadeira para quarta-feira", "2026-10-28 09:00", "Trabalho 2 da cadeira"),
])
def test_pt_pt_dates(text, at, rest):
    when, left = parse_when(text, NOW, LX)
    assert (when.astimezone(LX).strftime("%Y-%m-%d %H:%M"), left) == (at, rest)


@pytest.mark.parametrize("text, at", [  # as university emails write them (seen 2026-10-29)
    ("A data limite para entrega é 25-10-2026 23:59.", "2026-10-25 23:59"),
    ("A 1ª frequência será na próxima 3ª-feira, pelas 17:00", "2026-11-03 17:00"),
    ("teste na 6ª feira às 9h", "2026-10-30 09:00"),
])
def test_email_dates(text, at):
    when, _ = parse_when(text, NOW, LX, need_day=True)
    assert when.astimezone(LX).strftime("%Y-%m-%d %H:%M") == at


def test_a_written_date_wins_over_the_weekday_next_to_it():
    assert parse_when("14 de novembro (sábado), 23h30", NOW, LX, need_day=True)[0] == datetime(2026, 11, 14, 23, 30, tzinfo=LX)
    assert parse_when("sexta dia 6 de novembro às 10h", NOW, LX)[0] == datetime(2026, 11, 6, 10, 0, tzinfo=LX)


def test_a_time_without_a_day_is_no_date_when_a_day_is_needed():
    assert parse_when("next Tuesday at 17:00", NOW, LX, need_day=True)[0] is None
    assert parse_when("às 15h", NOW, LX)[0] is not None  # the assistant's "às 15h" still means today
    assert parse_when("Lab 2-3 na sala", NOW, LX)[0] is None  # dashes without a year are not a date


@pytest.mark.parametrize("text", ["coisa sem data", "pagar propinas dia 30 de fevereiro", "às 25h", "Lab 04"])
def test_no_date_or_an_impossible_one_is_none(text):
    assert parse_when(text, NOW, LX)[0] is None

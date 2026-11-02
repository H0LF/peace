"""When something is due, from the user's own pt-PT words: "sexta às 15h", "amanhã", "daqui a 2 horas", "dia 3",
"3 de novembro", "31/10", "ao meio-dia". Deterministic, so a misheard date is never invented by the model: the
model only copies the words, this module turns them into a time.
"""

import re
import unicodedata
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
MONTHS = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
          "novembro", "dezembro"]
NUMBERS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "dez": 10, "meia": 0.5}
HOURS = ["uma", "duas", "tres", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez", "onze", "doze"]  # Whisper writes some in words
AFTER_HOUR = {"", "horas", "hora", "de", "e", "hoje", "amanha", "na", "no", "para", "ate", "em"}
UNITS = {"minuto": 1 / 60, "min": 1 / 60, "hora": 1, "h": 1, "dia": 24, "semana": 168}

RELATIVE = re.compile(r"\b(?:daqui a|dentro de|em) (\d+|um|uma|dois|duas|tres|quatro|cinco|dez|meia) ?(minuto|min|hora|h|dia|semana)s?\b")
DAY_WORD = re.compile(r"\b(depois de amanha|amanha|hoje)\b")
# "3ª-feira" folds to "3a-feira": the ordinal form universities write (2ª = segunda … 6ª = sexta).
WEEKDAY = re.compile(r"\b(proxim[ao]\s+)?(" + "|".join(WEEKDAYS) + r"|[2-6]a(?=[-\s]feira))(?:[-\s]feira)?\b")
DATE = re.compile(r"\b(?:dia\s+(\d{1,2})(?:\s+de\s+(" + "|".join(MONTHS) + r"))?|(\d{1,2})\s+de\s+(" + "|".join(MONTHS)
                  + r")|(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?|(\d{1,2})-(\d{1,2})-(\d{4}))\b")  # dashes only with a year
TIME = re.compile(r"\b(\d{1,2})(?:[:h](\d{2})|h)(?:\s+da\s+(manha|tarde|noite))?\b"
                  r"|\b(?:as|pelas)\s+(\d{1,2}|" + "|".join(HOURS) + r")(?![:h\d])(?:\s+e\s+(meia))?(?:\s+da\s+(manha|tarde|noite))?\b"
                  r"|\b(meio-dia|meia-noite)\b")
# Words left hanging once the date is taken out: "entregar o relatório até sexta" -> "entregar o relatório".
DANGLING = re.compile(r"(?:\s+(?:ate|para|na|no|nesta|neste|esta|este|ao|as|a|pelas|de|em))+$|^(?:(?:ate|para|na|no|as|a)\s+)+")


ONLY_CONNECTORS = re.compile(r"(?:(?:ate|para|na|no|nesta|neste|esta|este|ao|as|a|pelas|de|em|e)\b\s*)*")


def fold(text: str) -> str:
    """Lowercase without accents, one character for one character (so spans map back to the original)."""
    return "".join(c.lower() if c.isascii() else unicodedata.normalize("NFKD", c)[0].lower() for c in text)


def _hour(h: int, minute: int, part: str | None) -> time | None:
    if part in ("tarde", "noite") and h < 12:
        h += 12
    return time(h, minute) if 0 <= h <= 23 and 0 <= minute <= 59 else None


def _next_month(day: date) -> date:
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return day.replace(year=year, month=month)


def _rest(text: str, spans: list[tuple[int, int]]) -> str:
    """The text without the date words; leftover connectors around each gap are dropped too."""
    pieces, last = [], 0
    for start, end in sorted(spans):
        pieces.append(text[last:start])
        last = end
    pieces.append(text[last:])
    cleaned = []
    for i, piece in enumerate(pieces):
        piece = piece.strip()
        if ONLY_CONNECTORS.fullmatch(fold(piece)):  # e.g. the "às" between "amanhã" and "15h"
            continue
        while True:  # connectors before a gap ("até", "na") and after it ("às")
            f = fold(piece)
            m = DANGLING.search(f) if i < len(pieces) - 1 else None
            n = re.match(r"^(?:(?:as|a|pelas|e)\s+)+", f) if i > 0 else None
            if m and m.group():
                piece = piece[: m.start()].strip()
            elif n:
                piece = piece[n.end():].strip()
            else:
                break
        cleaned.append(piece)
    return re.sub(r"\s+", " ", " ".join(p for p in cleaned if p)).strip(" ,.;:!?-")


def parse_when(text: str, now: datetime, tz: ZoneInfo, default_time: time = time(9, 0),
               need_day: bool = False) -> tuple[datetime | None, str]:
    """(the moment, the text without the words that said it). None when the text names no day or time, or, with
    `need_day`, names a time but no day (a date read from an email must not land on the day it was sent)."""
    f = fold(text)
    spans: list[tuple[int, int]] = []
    today = now.astimezone(tz).date()
    day: date | None = None
    at: time | None = None

    if m := RELATIVE.search(f):
        amount = NUMBERS[m[1]] if m[1] in NUMBERS else float(m[1])
        return now + timedelta(hours=amount * UNITS[m[2]]), _rest(text, [m.span()])
    if m := DAY_WORD.search(f):
        day = today + timedelta(days={"hoje": 0, "amanha": 1, "depois de amanha": 2}[m[1]])
        spans.append(m.span())
    elif m := DATE.search(f):
        d, month_name = (m[1], m[2]) if m[1] else (m[3], m[4]) if m[3] else (m[5], None) if m[5] else (m[8], None)
        number, given_year = (m[6], m[7]) if m[5] else (m[9], m[10])
        month = MONTHS.index(month_name) + 1 if month_name else int(number) if number else today.month
        year = int(given_year) + (2000 if int(given_year) < 100 else 0) if given_year else today.year
        try:
            day = date(year, month, int(d))
            if not given_year and day < today:  # "dia 3" late in October is 3 November; "15/1" in December, next January
                day = day.replace(year=year + 1) if month_name or number else _next_month(day)
        except ValueError:
            return None, text
        spans.append(m.span())
        if w := WEEKDAY.search(f):  # "14 de novembro (sábado)": the date says it, the weekday only repeats it
            spans.append(w.span())
    elif m := WEEKDAY.search(f):
        weekday = int(m[2][0]) - 2 if m[2][0].isdigit() else WEEKDAYS.index(m[2])
        ahead = (weekday - today.weekday()) % 7
        day = today + timedelta(days=ahead or (7 if m[1] else 0))
        spans.append(m.span())

    # An hour in words is a time only when no noun follows: "às duas" is 14h, "as duas pizzas" is not.
    m = next((t for t in TIME.finditer(f) if not t[4] or t[4].isdigit()
              or re.match(r"\s*(\w*)", f[t.end():])[1] in AFTER_HOUR), None)
    if m:
        if m[7]:
            at = time(12, 0) if m[7] == "meio-dia" else time(0, 0)
        elif m[1]:
            at = _hour(int(m[1]), int(m[2] or 0), m[3])
        else:  # a bare "às 5" means the afternoon unless "da manhã" says otherwise
            h = int(m[4]) if m[4].isdigit() else HOURS.index(m[4]) + 1
            at = _hour(h + 12 if 1 <= h <= 7 and m[6] is None else h, 30 if m[5] else 0, m[6])
        if at is None:
            return None, text
        spans.append(m.span())

    if day is None and (at is None or need_day):
        return None, text
    if day is None:  # only a time: today, or tomorrow if it has passed
        moment = datetime.combine(today, at, tz)
        return (moment if moment > now else moment + timedelta(days=1)), _rest(text, spans)
    return datetime.combine(day, at or default_time, tz), _rest(text, spans)

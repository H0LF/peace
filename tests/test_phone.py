from datetime import UTC, datetime, timedelta

import httpx
import pytest

from peace import phone
from peace.models import Notification, Preference

NOW = datetime(2026, 10, 26, 13, 0, tzinfo=UTC)  # Monday 13:00 in Lisbon, outside quiet hours


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(phone, "send", lambda prefs, title, message, urgent=False: out.append((prefs.topic, title, urgent)))
    return out


def reminder(session, **kw):
    session.add(Notification(key=kw.pop("key", "r1"), kind="reminder", title="Lembrete", body="Ligar à Ana", urgent=True,
                             deliver_after=NOW - timedelta(minutes=1), expires_at=NOW + timedelta(hours=1), **kw))
    session.commit()


def test_a_secret_topic_is_made_once_and_kept(client):
    first = client.get("/api/v1/preferences/phone").json()
    assert first["topic"].startswith("peace-") and len(first["topic"]) > 25 and not first["enabled"]
    saved = client.put("/api/v1/preferences/phone", json={"enabled": True, "server": "https://ntfy.sh", "topic": "", "mode": "always"}).json()
    assert saved["topic"] == first["topic"] and client.get("/api/v1/preferences/phone").json()["topic"] == first["topic"]
    for bad in ({"server": "http://ntfy.sh"}, {"topic": "short"}, {"mode": "never"}):
        assert client.put("/api/v1/preferences/phone", json=first | bad).status_code == 422


def test_away_mode_sends_only_while_the_pc_is_off_and_once(session, sent):
    prefs = phone.phone_prefs(session)
    session.get(Preference, "phone").value = prefs.model_copy(update={"enabled": True}).model_dump()
    session.add(Preference(key="desktop", value={"seen_at": (NOW - timedelta(seconds=20)).isoformat()}))
    reminder(session)
    assert phone.push_due(session, NOW) == 0  # the PC is on: it shows it there
    session.get(Preference, "desktop").value = {"seen_at": (NOW - timedelta(minutes=10)).isoformat()}
    session.commit()
    assert phone.push_due(session, NOW) == 1 and sent == [(prefs.topic, "Lembrete", True)]
    n = session.query(Notification).one()
    assert n.pushed_at == NOW and n.delivered_at == NOW  # the PC will not repeat it
    assert phone.push_due(session, NOW) == 0


def test_always_mode_keeps_the_pc_copy_and_a_failed_push_is_retried(session, monkeypatch):
    prefs = phone.phone_prefs(session).model_copy(update={"enabled": True, "mode": "always"})
    session.get(Preference, "phone").value = prefs.model_dump()
    reminder(session)

    def down(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(phone, "send", down)
    assert phone.push_due(session, NOW) == 0 and session.query(Notification).one().pushed_at is None
    monkeypatch.setattr(phone, "send", lambda *a, **k: None)
    assert phone.push_due(session, NOW) == 1
    assert session.query(Notification).one().delivered_at is None  # still shown on the PC too

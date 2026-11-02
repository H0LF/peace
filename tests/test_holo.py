"""Holo Hands switch: the button, the H key and the assistant share one flag the open dashboard follows."""

from datetime import timedelta

import pytest

from peace import assistant


def test_switch_and_status(client):
    assert client.get("/api/v1/holo").json() == {"on": False}
    assert client.put("/api/v1/holo", json={"on": True}).json() == {"on": True}
    assert client.get("/api/v1/assistant/status").json()["holo"] == {"on": True}
    client.put("/api/v1/holo", json={"on": False})
    assert client.get("/api/v1/holo").json() == {"on": False}


@pytest.mark.parametrize("text, command", [
    ("liga as mãos", "holo"), ("ativa os gestos", "holo"), ("liga o holo hands", "holo"),
    ("desliga as mãos", "holo_off"), ("desativa os gestos", "holo_off"), ("desliga o holo hands", "holo_off"),
])
def test_rules(text, command):
    assert assistant.match_rules(text, None)[0] == command


def test_voice_tells_when_no_dashboard_is_open(session, client):
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    assert "Abre o painel" in assistant.cmd_holo(session, now).text
    client.get("/api/v1/holo")  # the dashboard asks: now it is open
    assert assistant.cmd_holo(session, datetime.now(UTC)).text.startswith("Holo Hands ligado")
    assert "Abre o painel" in assistant.cmd_holo(session, datetime.now(UTC) + timedelta(seconds=30)).text
    assert assistant.cmd_holo_off(session, now).text == "Holo Hands desligado."
    assert client.get("/api/v1/holo").json() == {"on": False}

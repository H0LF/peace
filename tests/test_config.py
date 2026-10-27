import json
import logging

import pytest

from peace.config import database_url, get_settings
from peace.logs import JsonFormatter


def test_database_url_from_secret_file_keeps_special_characters(tmp_path, monkeypatch):
    secret = tmp_path / "db_password"
    secret.write_text("p@ss:w/rd%\n")
    monkeypatch.delenv("PEACE_DATABASE_URL")
    monkeypatch.setenv("PEACE_DB_PASSWORD_FILE", str(secret))
    url = database_url()
    assert (url.password, url.host, url.database) == ("p@ss:w/rd%", "db", "peace")


def test_short_api_token_is_rejected(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("PEACE_API_TOKEN", "short")
    try:
        with pytest.raises(RuntimeError):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_json_log_line_is_valid_json_with_unicode():
    record = logging.LogRecord("peace", logging.INFO, __file__, 1, "olá %s", ("mundo",), None)
    assert json.loads(JsonFormatter().format(record))["msg"] == "olá mundo"

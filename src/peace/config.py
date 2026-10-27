"""Runtime settings, read from environment variables."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.engine import URL, make_url


def database_url() -> URL:
    """PEACE_DATABASE_URL for dev/tests; otherwise built from the Compose secret file."""
    if url := os.environ.get("PEACE_DATABASE_URL"):
        return make_url(url)
    password = Path(os.environ["PEACE_DB_PASSWORD_FILE"]).read_text().strip()
    return URL.create(
        "postgresql+psycopg",
        username="peace",
        password=password,
        host=os.environ.get("PEACE_DB_HOST", "db"),
        port=5432,
        database="peace",
    )


@dataclass(frozen=True)
class Settings:
    api_token: str
    timezone: str
    log_level: str
    host: str
    port: int


@lru_cache
def get_settings() -> Settings:
    token = os.environ.get("PEACE_API_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("PEACE_API_TOKEN must be set and at least 32 characters long")
    return Settings(
        api_token=token,
        timezone=os.environ.get("PEACE_TIMEZONE", "Europe/Lisbon"),
        log_level=os.environ.get("PEACE_LOG_LEVEL", "INFO"),
        host=os.environ.get("PEACE_HOST", "0.0.0.0"),
        port=int(os.environ.get("PEACE_PORT", "8420")),
    )

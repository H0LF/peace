"""Adapters for external academic sources. Their content is untrusted data, never instructions."""

import html
import re


class SourceError(Exception):
    """The source could not be read completely; the sync changes nothing."""


class AuthError(SourceError):
    """Credentials were rejected or an interactive login (e.g. captcha) is required. Never retried."""


def text_of(fragment: str | None, limit: int = 2000) -> str:
    """HTML fragment to plain text (stored and shown as text, never rendered as HTML)."""
    plain = html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))
    return re.sub(r"\s+", " ", plain).strip()[:limit]

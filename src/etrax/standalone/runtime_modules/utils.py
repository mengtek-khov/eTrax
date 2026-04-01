"""Shared helpers used by standalone runtime module implementations."""

from __future__ import annotations


def normalize_parse_mode(raw: object) -> str | None:
    """Normalize a Telegram parse_mode value to ``None`` or cleaned text."""
    if raw is None:
        return None
    value = str(raw).strip()
    return value if value else None


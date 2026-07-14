"""Timezone-safe datetime helpers (UTC everywhere)."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime) -> datetime:
    """SQLite drivers may return naive datetimes; treat them as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

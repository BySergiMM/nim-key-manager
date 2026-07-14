"""Diagnostics: configuration normalization and time helpers."""

from datetime import datetime, timezone

from app.core.config import Settings
from app.core.timeutils import ensure_aware, utcnow


def test_database_url_normalization():
    s = Settings(database_url="postgres://u:p@host:5432/db")
    assert s.database_url.startswith("postgresql+asyncpg://")
    s = Settings(database_url="postgresql://u:p@host:5432/db")
    assert s.database_url.startswith("postgresql+asyncpg://")
    s = Settings(database_url="sqlite+aiosqlite:///x.db")
    assert s.database_url == "sqlite+aiosqlite:///x.db"


def test_empty_admin_env_treated_as_none():
    s = Settings(first_admin_email="", first_admin_password="")
    assert s.first_admin_email is None
    assert s.first_admin_password is None


def test_ensure_aware():
    naive = datetime(2026, 1, 1, 12, 0, 0)
    aware = ensure_aware(naive)
    assert aware.tzinfo is not None
    already = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert ensure_aware(already) is already


def test_utcnow_is_aware():
    assert utcnow().tzinfo is not None

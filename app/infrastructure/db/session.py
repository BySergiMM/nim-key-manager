"""Async engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import paths
from app.core.config import get_settings
from app.infrastructure.db.models import Base

_settings = get_settings()

_engine_kwargs: dict[str, Any] = {"pool_pre_ping": True}
if _settings.database_url.startswith("sqlite"):
    # NullPool avoids cross-event-loop connection reuse (tests, tooling).
    _engine_kwargs = {"poolclass": NullPool}
    # SQLite will not create missing directories; a fresh install points at
    # <home>/data/nimkm.db before anything has been written there.
    _db_file = paths.sqlite_path(_settings.database_url)
    if _db_file is not None:
        _db_file.parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(_settings.database_url, **_engine_kwargs)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

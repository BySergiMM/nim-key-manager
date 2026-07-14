"""Usage and inventory statistics."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.key_service import is_expiring_soon
from app.core.config import get_settings
from app.core.timeutils import ensure_aware, utcnow
from app.infrastructure.db.repositories import (
    ApiKeyRepository,
    ProjectRepository,
    UsageRepository,
    UserRepository,
)


class StatsService:
    def __init__(self, session: AsyncSession) -> None:
        self._keys = ApiKeyRepository(session)
        self._users = UserRepository(session)
        self._projects = ProjectRepository(session)
        self._usage = UsageRepository(session)

    async def overview(self) -> dict[str, Any]:
        settings = get_settings()
        keys = await self._keys.list_keys()
        status_counts = Counter(key.status for key in keys)
        return {
            "total_keys": len(keys),
            "keys_by_status": dict(status_counts),
            "keys_expiring_soon": sum(
                1 for key in keys if is_expiring_soon(key, settings.expiry_warning_days)
            ),
            "total_dispenses": sum(key.usage_count for key in keys),
            "total_users": await self._users.count(),
            "total_projects": len(await self._projects.list_all()),
        }

    async def usage_timeseries(self, days: int = 30) -> list[dict[str, Any]]:
        cutoff = utcnow() - timedelta(days=days)
        records = await self._usage.list_since(cutoff)
        counter = Counter(ensure_aware(r.created_at).date().isoformat() for r in records)
        return [{"date": day, "count": count} for day, count in sorted(counter.items())]

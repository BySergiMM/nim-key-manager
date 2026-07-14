"""Audit trail service: immutable events for every sensitive operation."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import AuditAction
from app.infrastructure.db.models import AuditLog, User
from app.infrastructure.db.repositories import AuditRepository


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = AuditRepository(session)

    async def record(
        self,
        action: AuditAction,
        *,
        actor: User | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        detail: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            actor_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
            action=action.value,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
            ip_address=ip_address,
        )
        return await self._repo.add(entry)

    async def list_entries(
        self, *, limit: int = 100, offset: int = 0, action: str | None = None
    ) -> list[AuditLog]:
        return await self._repo.list_entries(limit=limit, offset=offset, action=action)

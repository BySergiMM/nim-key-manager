"""User administration (admin-only operations)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.audit_service import AuditService
from app.core.security import hash_password
from app.domain.enums import AuditAction, Role
from app.domain.exceptions import ConflictError, NotFoundError
from app.infrastructure.db.models import User
from app.infrastructure.db.repositories import ApiKeyRepository, UserRepository


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._keys = ApiKeyRepository(session)
        self._audit = AuditService(session)

    async def list_users(self) -> list[User]:
        return await self._users.list_all()

    async def get(self, user_id: uuid.UUID) -> User:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("user not found")
        return user

    async def update(
        self,
        user_id: uuid.UUID,
        changes: dict[str, Any],
        *,
        actor: User,
        ip_address: str | None = None,
    ) -> User:
        user = await self.get(user_id)
        if self._is_admin_downgrade(user, changes):
            await self._ensure_other_admin(user)
        if "password" in changes:
            user.hashed_password = hash_password(changes.pop("password"))
        if "role" in changes:
            user.role = Role(changes.pop("role")).value
        for field in ("full_name", "is_active"):
            if field in changes:
                setattr(user, field, changes[field])
        await self._audit.record(
            AuditAction.USER_UPDATED,
            actor=actor,
            resource_type="user",
            resource_id=str(user.id),
            ip_address=ip_address,
        )
        await self._session.commit()
        return user

    async def delete(
        self, user_id: uuid.UUID, *, actor: User, ip_address: str | None = None
    ) -> None:
        user = await self.get(user_id)
        if user.role == Role.ADMIN.value:
            await self._ensure_other_admin(user)
        if await self._keys.count_by_owner(user.id) > 0:
            raise ConflictError("user still owns API keys; delete or reassign them first")
        await self._audit.record(
            AuditAction.USER_DELETED,
            actor=actor,
            resource_type="user",
            resource_id=str(user.id),
            detail={"email": user.email},
            ip_address=ip_address,
        )
        await self._users.delete(user)
        await self._session.commit()

    @staticmethod
    def _is_admin_downgrade(user: User, changes: dict[str, Any]) -> bool:
        if user.role != Role.ADMIN.value:
            return False
        role_changed = "role" in changes and Role(changes["role"]) is not Role.ADMIN
        deactivated = changes.get("is_active") is False
        return role_changed or deactivated

    async def _ensure_other_admin(self, user: User) -> None:
        admins = await self._users.count_active_admins()
        own = 1 if user.role == Role.ADMIN.value and user.is_active else 0
        if admins - own < 1:
            raise ConflictError("cannot remove or demote the last active administrator")

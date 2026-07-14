"""SQLAlchemy repository implementations (data access layer)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import KeyStatus, Role
from app.infrastructure.db.models import ApiKey, AuditLog, Project, UsageRecord, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count(self) -> int:
        return (await self._session.execute(select(func.count(User.id)))).scalar_one()

    async def count_active_admins(self) -> int:
        stmt = select(func.count(User.id)).where(
            User.role == Role.ADMIN.value, User.is_active.is_(True)
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[User]:
        stmt = select(User).order_by(User.created_at)
        return list((await self._session.execute(stmt)).scalars())

    async def add(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()
        return user

    async def delete(self, user: User) -> None:
        await self._session.delete(user)


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, project_id: uuid.UUID) -> Project | None:
        return await self._session.get(Project, project_id)

    async def get_by_name(self, name: str) -> Project | None:
        stmt = select(Project).where(Project.name == name)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[Project]:
        stmt = select(Project).order_by(Project.created_at)
        return list((await self._session.execute(stmt)).scalars())

    async def add(self, project: Project) -> Project:
        self._session.add(project)
        await self._session.flush()
        return project

    async def delete(self, project: Project) -> None:
        await self._session.delete(project)

    async def unassign_keys(self, project_id: uuid.UUID) -> None:
        stmt = update(ApiKey).where(ApiKey.project_id == project_id).values(project_id=None)
        await self._session.execute(stmt)


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key_id: uuid.UUID) -> ApiKey | None:
        return await self._session.get(ApiKey, key_id)

    async def get_by_fingerprint(self, value: str) -> ApiKey | None:
        stmt = select(ApiKey).where(ApiKey.fingerprint == value)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_keys(
        self, *, project_id: uuid.UUID | None = None, status: str | None = None
    ) -> list[ApiKey]:
        stmt = select(ApiKey).order_by(ApiKey.created_at)
        if project_id is not None:
            stmt = stmt.where(ApiKey.project_id == project_id)
        if status is not None:
            stmt = stmt.where(ApiKey.status == status)
        return list((await self._session.execute(stmt)).scalars())

    async def count_by_owner(self, owner_id: uuid.UUID) -> int:
        stmt = select(func.count(ApiKey.id)).where(ApiKey.owner_id == owner_id)
        return (await self._session.execute(stmt)).scalar_one()

    async def pick_available(
        self, *, project_id: uuid.UUID | None, now: datetime
    ) -> ApiKey | None:
        """Least-recently-used active key, optionally scoped to a project."""
        stmt = (
            select(ApiKey)
            .where(
                ApiKey.status == KeyStatus.ACTIVE.value,
                or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
            )
            .order_by(ApiKey.last_used_at.asc().nulls_first(), ApiKey.usage_count.asc())
            .limit(1)
        )
        if project_id is not None:
            stmt = stmt.where(ApiKey.project_id == project_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def clear_rotation_links(self, key_id: uuid.UUID) -> None:
        stmt = (
            update(ApiKey).where(ApiKey.rotated_from_id == key_id).values(rotated_from_id=None)
        )
        await self._session.execute(stmt)

    async def add(self, key: ApiKey) -> ApiKey:
        self._session.add(key)
        await self._session.flush()
        return key

    async def delete(self, key: ApiKey) -> None:
        await self._session.delete(key)


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: AuditLog) -> AuditLog:
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def list_entries(
        self, *, limit: int = 100, offset: int = 0, action: str | None = None
    ) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        return list((await self._session.execute(stmt)).scalars())


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: UsageRecord) -> UsageRecord:
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_since(self, cutoff: datetime) -> list[UsageRecord]:
        stmt = select(UsageRecord).where(UsageRecord.created_at >= cutoff)
        return list((await self._session.execute(stmt)).scalars())

    async def delete_for_key(self, key_id: uuid.UUID) -> None:
        for record in (
            await self._session.execute(
                select(UsageRecord).where(UsageRecord.api_key_id == key_id)
            )
        ).scalars():
            await self._session.delete(record)

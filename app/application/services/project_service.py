"""Projects group API keys for different consumers/workloads."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.audit_service import AuditService
from app.domain.enums import AuditAction
from app.domain.exceptions import ConflictError, NotFoundError
from app.infrastructure.db.models import ApiKey, Project, User
from app.infrastructure.db.repositories import ApiKeyRepository, ProjectRepository


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectRepository(session)
        self._keys = ApiKeyRepository(session)
        self._audit = AuditService(session)

    async def create(
        self,
        *,
        name: str,
        description: str | None,
        owner: User,
        ip_address: str | None = None,
    ) -> Project:
        if await self._projects.get_by_name(name) is not None:
            raise ConflictError("a project with this name already exists")
        project = Project(name=name, description=description, owner_id=owner.id)
        await self._projects.add(project)
        await self._audit.record(
            AuditAction.PROJECT_CREATED,
            actor=owner,
            resource_type="project",
            resource_id=str(project.id),
            detail={"name": name},
            ip_address=ip_address,
        )
        await self._session.commit()
        return project

    async def get(self, project_id: uuid.UUID) -> Project:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("project not found")
        return project

    async def list_projects(self) -> list[Project]:
        return await self._projects.list_all()

    async def update(
        self,
        project_id: uuid.UUID,
        changes: dict[str, Any],
        *,
        actor: User,
        ip_address: str | None = None,
    ) -> Project:
        project = await self.get(project_id)
        new_name = changes.get("name")
        if new_name and new_name != project.name:
            if await self._projects.get_by_name(new_name) is not None:
                raise ConflictError("a project with this name already exists")
            project.name = new_name
        if "description" in changes:
            project.description = changes["description"]
        await self._audit.record(
            AuditAction.PROJECT_UPDATED,
            actor=actor,
            resource_type="project",
            resource_id=str(project.id),
            ip_address=ip_address,
        )
        await self._session.commit()
        return project

    async def delete(
        self, project_id: uuid.UUID, *, actor: User, ip_address: str | None = None
    ) -> None:
        project = await self.get(project_id)
        await self._projects.unassign_keys(project.id)
        await self._audit.record(
            AuditAction.PROJECT_DELETED,
            actor=actor,
            resource_type="project",
            resource_id=str(project.id),
            detail={"name": project.name},
            ip_address=ip_address,
        )
        await self._projects.delete(project)
        await self._session.commit()

    async def assign_key(
        self,
        project_id: uuid.UUID,
        key_id: uuid.UUID,
        *,
        actor: User,
        ip_address: str | None = None,
    ) -> ApiKey:
        project = await self.get(project_id)
        key = await self._keys.get(key_id)
        if key is None:
            raise NotFoundError("API key not found")
        key.project_id = project.id
        await self._audit.record(
            AuditAction.PROJECT_UPDATED,
            actor=actor,
            resource_type="project",
            resource_id=str(project.id),
            detail={"assigned_key": str(key.id)},
            ip_address=ip_address,
        )
        await self._session.commit()
        return key

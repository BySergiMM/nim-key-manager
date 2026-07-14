"""Project endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, require_role
from app.api.schemas import KeyOut, ProjectCreate, ProjectOut, ProjectUpdate
from app.application.services.project_service import ProjectService
from app.domain.enums import Role
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_session

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> list[ProjectOut]:
    return [ProjectOut.model_validate(p) for p in await ProjectService(session).list_projects()]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.MANAGER)),
) -> ProjectOut:
    project = await ProjectService(session).create(
        name=body.name,
        description=body.description,
        owner=user,
        ip_address=client_ip(request),
    )
    return ProjectOut.model_validate(project)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> ProjectOut:
    return ProjectOut.model_validate(await ProjectService(session).get(project_id))


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.MANAGER)),
) -> ProjectOut:
    project = await ProjectService(session).update(
        project_id,
        body.model_dump(exclude_unset=True),
        actor=user,
        ip_address=client_ip(request),
    )
    return ProjectOut.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.MANAGER)),
) -> None:
    await ProjectService(session).delete(project_id, actor=user, ip_address=client_ip(request))


@router.post("/{project_id}/keys/{key_id}", response_model=KeyOut)
async def assign_key(
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.MANAGER)),
) -> KeyOut:
    key = await ProjectService(session).assign_key(
        project_id, key_id, actor=user, ip_address=client_ip(request)
    )
    return KeyOut.from_model(key)

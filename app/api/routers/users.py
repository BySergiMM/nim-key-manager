"""User administration endpoints (admin only)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, require_role
from app.api.schemas import UserCreate, UserOut, UserUpdate
from app.application.services.auth_service import AuthService
from app.application.services.user_service import UserService
from app.domain.enums import Role
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_session

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_role(Role.ADMIN)),
) -> list[User]:
    return await UserService(session).list_users()


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_role(Role.ADMIN)),
) -> User:
    return await AuthService(session).register(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=body.role,
        actor=admin,
        ip_address=client_ip(request),
    )


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_role(Role.ADMIN)),
) -> User:
    return await UserService(session).get(user_id)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_role(Role.ADMIN)),
) -> User:
    changes = body.model_dump(exclude_unset=True)
    return await UserService(session).update(
        user_id, changes, actor=admin, ip_address=client_ip(request)
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_role(Role.ADMIN)),
) -> None:
    await UserService(session).delete(user_id, actor=admin, ip_address=client_ip(request))

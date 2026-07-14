"""FastAPI dependencies: auth, RBAC, gateways, request helpers."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces import KeyValidator
from app.core.security import decode_token
from app.domain.enums import ROLE_HIERARCHY, Role
from app.domain.exceptions import InvalidTokenError
from app.infrastructure.db.models import User
from app.infrastructure.db.repositories import UserRepository
from app.infrastructure.db.session import get_session
from app.infrastructure.nvidia.client import NvidiaKeyValidator

bearer_scheme = HTTPBearer(auto_error=False)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User | None:
    if credentials is None:
        return None
    try:
        payload = decode_token(credentials.credentials)
        user = await UserRepository(session).get(uuid.UUID(payload["sub"]))
    except (InvalidTokenError, ValueError):
        return None
    if user is None or not user.is_active:
        return None
    return user


async def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(minimum: Role) -> Callable[..., Awaitable[User]]:
    async def checker(user: User = Depends(get_current_user)) -> User:
        if ROLE_HIERARCHY[Role(user.role)] < ROLE_HIERARCHY[minimum]:
            raise HTTPException(status_code=403, detail="insufficient permissions")
        return user

    return checker


def get_key_validator() -> KeyValidator:
    return NvidiaKeyValidator()


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None

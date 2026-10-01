"""FastAPI dependencies: auth, RBAC, gateways, request helpers."""

from __future__ import annotations

import hmac
import ipaddress
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces import KeyValidator
from app.core.config import get_settings
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


async def require_metrics_access(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Guard ``/metrics``: the ``METRICS_TOKEN`` (for a Prometheus scraper) or an admin's JWT.

    The metrics name every route and carry latencies and status codes per handler, which is
    reconnaissance material, so they are not public. A scraper sends the token as
    ``Authorization: Bearer <METRICS_TOKEN>`` (Prometheus: ``authorization.credentials``).
    """
    unauthenticated = HTTPException(
        status_code=401, detail="not authenticated", headers={"WWW-Authenticate": "Bearer"}
    )
    if credentials is None:
        raise unauthenticated
    expected = get_settings().metrics_token
    # compare_digest on bytes: constant time, and no TypeError for a non-ASCII header value.
    if expected and hmac.compare_digest(credentials.credentials.encode(), expected.encode()):
        return
    user = await get_optional_user(credentials, session)
    if user is None:
        raise unauthenticated
    if user.role != Role.ADMIN.value:
        raise HTTPException(status_code=403, detail="insufficient permissions")


def get_key_validator() -> KeyValidator:
    return NvidiaKeyValidator()


def client_ip(request: Request) -> str | None:
    """The client address to write to the audit log: a valid IP address, or ``None``.

    Behind a trusted proxy uvicorn replaces the peer address with the entry it takes from
    ``X-Forwarded-For``. It strips the brackets and port but does not check that what is left is
    an address, so a proxy that forwards the header without appending to it hands the application
    whatever the client wrote (HTML, or text longer than the ``audit_logs.ip_address`` column).
    That is validated here. uvicorn also drops the original peer address, so there is nothing to
    fall back to and an entry that is not an address is recorded as unknown.
    """
    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if getattr(address, "scope_id", None):  # fe80::1%<anything>: not a client address
        return None
    return str(address)

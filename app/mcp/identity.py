"""Bridge the MCP OAuth identity to the application's user / RBAC / audit model.

Every MCP tool runs *as* an application ``User``: the OAuth identity presented by
Claude (GitHub login / e-mail, or Google e-mail) is matched against an allow-list
and resolved to a ``User`` row, so the existing services enforce roles and write
audit entries exactly as they do for the REST API.
"""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.domain.enums import ROLE_HIERARCHY, Role
from app.domain.exceptions import PermissionDeniedError
from app.infrastructure.db.models import User
from app.infrastructure.db.repositories import UserRepository
from app.infrastructure.db.session import SessionFactory


@dataclass(slots=True)
class Identity:
    """Normalized OAuth identity extracted from the FastMCP access token."""

    subject: str
    login: str | None
    email: str | None
    name: str | None


def extract_identity() -> Identity | None:
    """Read the authenticated identity from the current request context.

    Returns ``None`` when no token is present (auth disabled / dev mode).
    """
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    if token is None:
        return None
    claims = token.claims or {}
    subject = token.subject or str(claims.get("sub") or claims.get("id") or "")
    return Identity(
        subject=subject,
        login=claims.get("login"),
        email=claims.get("email"),
        name=claims.get("name"),
    )


def resolve_email(identity: Identity) -> str:
    """Deterministic e-mail used as the app user's unique key."""
    if identity.email:
        return identity.email.lower()
    if identity.login:
        return f"{identity.login.lower()}@users.noreply.github.com"
    return f"{identity.subject}@mcp.local"


def is_allowed(identity: Identity, settings: Settings) -> bool:
    """Fail-closed allow-list check against configured logins/e-mails."""
    allow = {item.strip().lower() for item in settings.mcp_allowed_identities if item.strip()}
    if not allow:
        return False
    candidates = {
        value.lower()
        for value in (identity.login, identity.email, resolve_email(identity))
        if value
    }
    return bool(candidates & allow)


async def resolve_actor(session: AsyncSession, settings: Settings) -> User:
    """Resolve the application ``User`` that a tool call acts as."""
    users = UserRepository(session)

    if not settings.mcp_auth_enabled:
        return await _resolve_dev_actor(users, settings)

    identity = extract_identity()
    if identity is None:  # pragma: no cover - auth middleware guarantees a token
        raise PermissionDeniedError("authentication required")
    if not is_allowed(identity, settings):
        raise PermissionDeniedError("this identity is not allowed to use the connector")

    email = resolve_email(identity)
    user = await users.get_by_email(email)
    if user is not None:
        if not user.is_active:
            raise PermissionDeniedError("this user is deactivated")
        return user

    if not settings.mcp_auto_provision:
        raise PermissionDeniedError("no matching user and auto-provisioning is disabled")

    user = User(
        email=email,
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        full_name=identity.name,
        role=settings.mcp_default_role.value,
    )
    await users.add(user)
    await session.commit()
    return user


async def _resolve_dev_actor(users: UserRepository, settings: Settings) -> User:
    """Actor resolution when OAuth is disabled (local dev / tests)."""
    if settings.mcp_dev_identity:
        user = await users.get_by_email(settings.mcp_dev_identity.lower())
        if user is not None:
            return user
    for candidate in await users.list_all():
        if candidate.role == Role.ADMIN.value and candidate.is_active:
            return candidate
    raise PermissionDeniedError("no active admin user available for the dev MCP actor")


def require_role(actor: User, minimum: Role) -> None:
    """RBAC gate mirroring ``app.api.deps.require_role``."""
    if ROLE_HIERARCHY[Role(actor.role)] < ROLE_HIERARCHY[minimum]:
        raise PermissionDeniedError("insufficient permissions for this operation")


@contextlib.asynccontextmanager
async def tool_context(minimum: Role) -> AsyncIterator[tuple[AsyncSession, User]]:
    """Open a session, resolve the actor and enforce the minimum role.

    Yields ``(session, actor)`` for the tool body. The session is committed by
    the services themselves; it is always closed on exit.
    """
    settings = get_settings()
    async with SessionFactory() as session:
        actor = await resolve_actor(session, settings)
        require_role(actor, minimum)
        yield session, actor

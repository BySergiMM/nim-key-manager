"""Bridge the MCP OAuth identity to the application's user / RBAC / audit model.

Every MCP tool runs *as* an application ``User``: the OAuth identity presented by
Claude (GitHub or Google account) is matched against an allow-list and resolved to a
``User`` row, so the existing services enforce roles and write audit entries exactly as
they do for the REST API.

The allow-list holds stable account ids (GitHub's numeric user id, Google's ``sub``) and
e-mail addresses the provider vouches for. It never holds GitHub logins: a login is not an
identity, it can be renamed and then registered by somebody else.
"""

from __future__ import annotations

import contextlib
import re
import secrets
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import hash_password
from app.domain.enums import ROLE_HIERARCHY, Role
from app.domain.exceptions import PermissionDeniedError
from app.infrastructure.db.models import User
from app.infrastructure.db.repositories import UserRepository
from app.infrastructure.db.session import SessionFactory

logger = get_logger(__name__)

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")


@dataclass(slots=True)
class Identity:
    """Normalized OAuth identity extracted from the FastMCP access token."""

    subject: str  # the provider's stable account id: GitHub user id, Google ``sub``
    login: str | None
    email: str | None
    name: str | None
    email_verified: bool | None = None  # None: the provider did not say


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
        email_verified=_as_flag(claims.get("email_verified")),
    )


def _as_flag(value: object) -> bool | None:
    """Providers report ``email_verified`` as a boolean or as the string ``"true"``."""
    if value is None:
        return None
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def resolve_email(identity: Identity) -> str:
    """Deterministic e-mail used as the app user's unique key.

    An address the provider says it has not verified is not a key: using it would let an
    account that is allowed by id act as the application user that owns that address.
    """
    if identity.email and identity.email_verified is not False:
        return identity.email.lower()
    if identity.login:
        return f"{identity.login.lower()}@users.noreply.github.com"
    return f"{identity.subject}@mcp.local"


@dataclass(frozen=True, slots=True)
class AllowList:
    """``MCP_ALLOWED_IDENTITIES`` sorted by what each entry can match."""

    ids: frozenset[str]  # all digits: the account id (GitHub user id, Google ``sub``)
    emails: frozenset[str]  # lower-cased addresses
    ignored: tuple[str, ...]  # anything else, i.e. GitHub logins: never matched


def parse_allow_list(entries: Iterable[str]) -> AllowList:
    ids: set[str] = set()
    emails: set[str] = set()
    ignored: list[str] = []
    for raw in entries:
        entry = raw.strip()
        if not entry:
            continue
        if entry.isascii() and entry.isdigit():
            ids.add(entry)
        elif _EMAIL.fullmatch(entry):
            emails.add(entry.lower())
        else:
            ignored.append(entry)
    return AllowList(frozenset(ids), frozenset(emails), tuple(ignored))


def _email_is_vouched_for(identity: Identity, settings: Settings) -> bool:
    """Whether the provider stands behind ``identity.email``.

    Google says so explicitly (``email_verified``) and an address it does not vouch for never
    matches. GitHub reports the public profile address without such a flag; GitHub only lets a
    user publish one of their verified addresses (an inference from GitHub's documentation, not
    something the API states), so it is accepted unless the claim says otherwise.
    """
    if identity.email_verified is not None:
        return identity.email_verified
    return settings.mcp_auth_provider == "github"


def is_allowed(identity: Identity, settings: Settings) -> bool:
    """Fail-closed allow-list check.

    A numeric entry matches the account id and nothing else: not the login, which may well be
    all digits on somebody else's account. An e-mail entry matches the provider's e-mail for
    the account when the provider vouches for it. Login entries never match.
    """
    allow = parse_allow_list(settings.mcp_allowed_identities)
    if identity.subject and identity.subject in allow.ids:
        return True
    return bool(
        identity.email
        and identity.email.strip().lower() in allow.emails
        and _email_is_vouched_for(identity, settings)
    )


async def resolve_actor(session: AsyncSession, settings: Settings) -> User:
    """Resolve the application ``User`` that a tool call acts as."""
    users = UserRepository(session)

    if not settings.mcp_auth_enabled:
        return await _resolve_dev_actor(users, settings)

    identity = extract_identity()
    if identity is None:  # pragma: no cover - auth middleware guarantees a token
        raise PermissionDeniedError("authentication required")
    if not is_allowed(identity, settings):
        # The operator needs the account id to put in MCP_ALLOWED_IDENTITIES, and it is only
        # a number the denied person already has; it stays in the log, not in the error.
        logger.warning(
            "mcp_identity_denied",
            subject=identity.subject,
            login=identity.login,
            detail="add the numeric subject to MCP_ALLOWED_IDENTITIES to allow this account",
        )
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

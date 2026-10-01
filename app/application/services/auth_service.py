"""Authentication: registration, login, token refresh."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.audit_service import AuditService
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.domain.enums import AuditAction, Role
from app.domain.exceptions import (
    ConflictError,
    InvalidCredentialsError,
    InvalidTokenError,
    PermissionDeniedError,
)
from app.infrastructure.db.models import User
from app.infrastructure.db.repositories import UserRepository


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._audit = AuditService(session)

    async def register(
        self,
        *,
        email: str,
        password: str,
        full_name: str | None,
        role: Role,
        actor: User | None,
        ip_address: str | None = None,
    ) -> User:
        """Create a user. Only an administrator may.

        There is deliberately no "the first caller becomes the administrator" rule here: on a
        fresh installation whoever reached ``POST /api/v1/auth/register`` first would take it
        over. The first administrator comes from ``bootstrap_admin``, which only code running on
        the host calls (never an HTTP request).
        """
        if actor is None or actor.role != Role.ADMIN.value:
            raise PermissionDeniedError("only administrators can register new users")
        return await self._create(
            email=email,
            password=password,
            full_name=full_name,
            role=role,
            actor=actor,
            ip_address=ip_address,
        )

    async def bootstrap_admin(
        self, *, email: str, password: str, full_name: str | None = None
    ) -> User:
        """Create the very first user, as ADMIN, on an installation that has no users.

        For trusted code that runs on the host: the ``FIRST_ADMIN_EMAIL`` /
        ``FIRST_ADMIN_PASSWORD`` seeding at start-up and ``scripts/create_admin.py``. Nothing
        reachable over HTTP may call it.
        """
        if await self._users.count() != 0:
            raise ConflictError(
                "the first administrator can only be created while there are no users"
            )
        return await self._create(
            email=email,
            password=password,
            full_name=full_name,
            role=Role.ADMIN,
            actor=None,
        )

    async def _create(
        self,
        *,
        email: str,
        password: str,
        full_name: str | None,
        role: Role,
        actor: User | None,
        ip_address: str | None = None,
    ) -> User:
        if await self._users.get_by_email(email) is not None:
            raise ConflictError("a user with this email already exists")
        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role=role.value,
        )
        await self._users.add(user)
        await self._audit.record(
            AuditAction.USER_REGISTERED,
            actor=actor or user,
            resource_type="user",
            resource_id=str(user.id),
            detail={"email": email, "role": role.value},
            ip_address=ip_address,
        )
        await self._session.commit()
        return user

    async def login(
        self, *, email: str, password: str, ip_address: str | None = None
    ) -> tuple[str, str]:
        user = await self._users.get_by_email(email)
        if user is None or not user.is_active or not verify_password(
            password, user.hashed_password
        ):
            raise InvalidCredentialsError("invalid email or password")
        await self._audit.record(AuditAction.USER_LOGIN, actor=user, ip_address=ip_address)
        await self._session.commit()
        return self._issue_tokens(user)

    async def refresh(self, refresh_token: str) -> tuple[str, str]:
        payload = decode_token(refresh_token, expected_type="refresh")
        user = await self._users.get(uuid.UUID(payload["sub"]))
        if user is None or not user.is_active:
            raise InvalidTokenError("user no longer exists or is inactive")
        return self._issue_tokens(user)

    @staticmethod
    def _issue_tokens(user: User) -> tuple[str, str]:
        subject = str(user.id)
        return (
            create_token(subject, user.role, "access"),
            create_token(subject, user.role, "refresh"),
        )

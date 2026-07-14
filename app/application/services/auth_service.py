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
        """Create a user.

        The very first user of the system self-registers and becomes ADMIN
        (bootstrap). Afterwards only administrators may create users.
        """
        is_bootstrap = await self._users.count() == 0
        if not is_bootstrap and (actor is None or actor.role != Role.ADMIN.value):
            raise PermissionDeniedError("only administrators can register new users")
        if await self._users.get_by_email(email) is not None:
            raise ConflictError("a user with this email already exists")
        effective_role = Role.ADMIN if is_bootstrap else role
        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role=effective_role.value,
        )
        await self._users.add(user)
        await self._audit.record(
            AuditAction.USER_REGISTERED,
            actor=actor or user,
            resource_type="user",
            resource_id=str(user.id),
            detail={"email": email, "role": effective_role.value},
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

"""API key lifecycle: registration, validation, rotation, dispensing, expiry.

NVIDIA Build does not offer official programmatic key creation/rotation, so
rotation here is *assisted*: the operator creates the new key in their NVIDIA
account and this service performs an atomic, audited swap (new key becomes
active, old key is revoked and archived with a lineage link).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces import KeyValidationOutcome, KeyValidator
from app.application.services.audit_service import AuditService
from app.core.config import get_settings
from app.core.crypto import SecretCipher, fingerprint, hint
from app.core.timeutils import ensure_aware, utcnow
from app.domain.enums import AuditAction, KeyCheckResult, KeyStatus
from app.domain.exceptions import (
    ConflictError,
    NoKeyAvailableError,
    NotFoundError,
    ValidationFailedError,
)
from app.infrastructure.db.models import ApiKey, UsageRecord, User
from app.infrastructure.db.repositories import (
    ApiKeyRepository,
    ProjectRepository,
    UsageRepository,
)

NVAPI_PREFIX = "nvapi-"
# The REST schemas accept 20 to 512 characters (KeyCreate, KeyRotate). The service enforces the
# same range for every caller: the connector's tools take the key as a plain string.
MIN_API_KEY_LENGTH = 20
MAX_API_KEY_LENGTH = 512


def is_expiring_soon(key: ApiKey, warning_days: int) -> bool:
    if key.expires_at is None or key.status != KeyStatus.ACTIVE.value:
        return False
    return ensure_aware(key.expires_at) <= utcnow() + timedelta(days=warning_days)


class KeyService:
    def __init__(self, session: AsyncSession, validator: KeyValidator | None = None) -> None:
        self._session = session
        self._keys = ApiKeyRepository(session)
        self._projects = ProjectRepository(session)
        self._usage = UsageRepository(session)
        self._audit = AuditService(session)
        self._validator = validator
        self._cipher = SecretCipher(get_settings().encryption_master_key)

    async def register(
        self,
        *,
        name: str,
        api_key: str,
        owner: User,
        project_id: uuid.UUID | None = None,
        expires_at: datetime | None = None,
        validate_remote: bool = False,
        ip_address: str | None = None,
    ) -> ApiKey:
        self._ensure_format(api_key)
        if await self._keys.get_by_fingerprint(fingerprint(api_key)) is not None:
            raise ConflictError("this API key is already registered")
        if project_id is not None and await self._projects.get(project_id) is None:
            raise NotFoundError("project not found")
        last_validated_at: datetime | None = None
        if validate_remote:
            outcome = await self._require_validator().validate(api_key)
            if outcome.result is KeyCheckResult.INVALID:
                raise ValidationFailedError("NVIDIA rejected this API key (invalid or revoked)")
            if outcome.result is KeyCheckResult.VALID:
                last_validated_at = utcnow()
        key = ApiKey(
            name=name,
            encrypted_key=self._cipher.encrypt(api_key),
            fingerprint=fingerprint(api_key),
            key_hint=hint(api_key),
            status=KeyStatus.ACTIVE.value,
            expires_at=expires_at,
            last_validated_at=last_validated_at,
            owner_id=owner.id,
            project_id=project_id,
        )
        await self._keys.add(key)
        await self._audit.record(
            AuditAction.KEY_REGISTERED,
            actor=owner,
            resource_type="api_key",
            resource_id=str(key.id),
            detail={"name": name, "hint": key.key_hint},
            ip_address=ip_address,
        )
        await self._session.commit()
        return key

    async def get(self, key_id: uuid.UUID) -> ApiKey:
        key = await self._keys.get(key_id)
        if key is None:
            raise NotFoundError("API key not found")
        return key

    async def list_keys(
        self, *, project_id: uuid.UUID | None = None, status: str | None = None
    ) -> list[ApiKey]:
        return await self._keys.list_keys(project_id=project_id, status=status)

    async def validate(
        self, key_id: uuid.UUID, *, actor: User, ip_address: str | None = None
    ) -> tuple[ApiKey, KeyValidationOutcome]:
        key = await self.get(key_id)
        outcome = await self._require_validator().validate(
            self._cipher.decrypt(key.encrypted_key)
        )
        key.last_validated_at = utcnow()
        if outcome.result is KeyCheckResult.INVALID and key.status == KeyStatus.ACTIVE.value:
            key.status = KeyStatus.INVALID.value
        elif outcome.result is KeyCheckResult.VALID and key.status == KeyStatus.INVALID.value:
            key.status = KeyStatus.ACTIVE.value
        await self._audit.record(
            AuditAction.KEY_VALIDATED,
            actor=actor,
            resource_type="api_key",
            resource_id=str(key.id),
            detail={"result": outcome.result.value},
            ip_address=ip_address,
        )
        await self._session.commit()
        return key, outcome

    async def rotate(
        self,
        key_id: uuid.UUID,
        *,
        new_api_key: str,
        actor: User,
        expires_at: datetime | None = None,
        ip_address: str | None = None,
    ) -> ApiKey:
        old = await self.get(key_id)
        self._ensure_format(new_api_key)
        if await self._keys.get_by_fingerprint(fingerprint(new_api_key)) is not None:
            raise ConflictError("the replacement key is already registered")
        replacement = ApiKey(
            name=old.name,
            encrypted_key=self._cipher.encrypt(new_api_key),
            fingerprint=fingerprint(new_api_key),
            key_hint=hint(new_api_key),
            status=KeyStatus.ACTIVE.value,
            expires_at=expires_at,
            owner_id=actor.id,
            project_id=old.project_id,
            rotated_from_id=old.id,
        )
        old.status = KeyStatus.REVOKED.value
        old.revoked_at = utcnow()
        await self._keys.add(replacement)
        await self._audit.record(
            AuditAction.KEY_ROTATED,
            actor=actor,
            resource_type="api_key",
            resource_id=str(replacement.id),
            detail={"replaces": str(old.id)},
            ip_address=ip_address,
        )
        await self._session.commit()
        return replacement

    async def revoke(
        self, key_id: uuid.UUID, *, actor: User, ip_address: str | None = None
    ) -> ApiKey:
        key = await self.get(key_id)
        if key.status == KeyStatus.REVOKED.value:
            raise ConflictError("key is already revoked")
        key.status = KeyStatus.REVOKED.value
        key.revoked_at = utcnow()
        await self._audit.record(
            AuditAction.KEY_REVOKED,
            actor=actor,
            resource_type="api_key",
            resource_id=str(key.id),
            ip_address=ip_address,
        )
        await self._session.commit()
        return key

    async def delete(
        self, key_id: uuid.UUID, *, actor: User, ip_address: str | None = None
    ) -> None:
        key = await self.get(key_id)
        await self._usage.delete_for_key(key.id)
        await self._keys.clear_rotation_links(key.id)
        await self._audit.record(
            AuditAction.KEY_DELETED,
            actor=actor,
            resource_type="api_key",
            resource_id=str(key.id),
            detail={"name": key.name, "hint": key.key_hint},
            ip_address=ip_address,
        )
        await self._keys.delete(key)
        await self._session.commit()

    async def dispense(
        self,
        *,
        project_id: uuid.UUID | None = None,
        actor: User,
        ip_address: str | None = None,
    ) -> tuple[ApiKey, str]:
        """Return the least-recently-used active key (decrypted) and record usage."""
        now = utcnow()
        key = await self._keys.pick_available(project_id=project_id, now=now)
        if key is None:
            scope = " for this project" if project_id is not None else ""
            raise NoKeyAvailableError(f"no active API key available{scope}")
        plaintext = self._cipher.decrypt(key.encrypted_key)
        key.last_used_at = now
        key.usage_count += 1
        await self._usage.add(
            UsageRecord(api_key_id=key.id, project_id=key.project_id, action="dispense")
        )
        await self._audit.record(
            AuditAction.KEY_DISPENSED,
            actor=actor,
            resource_type="api_key",
            resource_id=str(key.id),
            ip_address=ip_address,
        )
        await self._session.commit()
        return key, plaintext

    async def check_expirations(self) -> list[ApiKey]:
        """Mark active keys past their expiry date as EXPIRED (audited)."""
        now = utcnow()
        newly_expired: list[ApiKey] = []
        for key in await self._keys.list_keys(status=KeyStatus.ACTIVE.value):
            if key.expires_at is not None and ensure_aware(key.expires_at) <= now:
                key.status = KeyStatus.EXPIRED.value
                newly_expired.append(key)
                await self._audit.record(
                    AuditAction.KEY_EXPIRED, resource_type="api_key", resource_id=str(key.id)
                )
        if newly_expired:
            await self._session.commit()
        return newly_expired

    async def validate_all_active(self) -> int:
        """Validate every active key against NVIDIA; returns number checked."""
        checked = 0
        for key in await self._keys.list_keys(status=KeyStatus.ACTIVE.value):
            outcome = await self._require_validator().validate(
                self._cipher.decrypt(key.encrypted_key)
            )
            key.last_validated_at = utcnow()
            if outcome.result is KeyCheckResult.INVALID:
                key.status = KeyStatus.INVALID.value
            checked += 1
        await self._session.commit()
        return checked

    @staticmethod
    def _ensure_format(api_key: str) -> None:
        if not api_key.startswith(NVAPI_PREFIX) or not (
            MIN_API_KEY_LENGTH <= len(api_key) <= MAX_API_KEY_LENGTH
        ):
            # Never echo the value: it is a secret.
            raise ValidationFailedError(
                f"API key must start with 'nvapi-' and be {MIN_API_KEY_LENGTH} to "
                f"{MAX_API_KEY_LENGTH} characters long"
            )

    def _require_validator(self) -> KeyValidator:
        if self._validator is None:  # pragma: no cover - defensive
            raise RuntimeError("no key validator configured")
        return self._validator

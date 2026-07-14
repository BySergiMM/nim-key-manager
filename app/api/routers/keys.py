"""API key lifecycle endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_key_validator, require_role
from app.api.rate_limit import limiter
from app.api.schemas import DispensedKey, KeyCreate, KeyOut, KeyRotate, KeyValidationOut
from app.application.interfaces import KeyValidator
from app.application.services.key_service import KeyService
from app.core.config import get_settings
from app.domain.enums import KeyStatus, Role
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_session

router = APIRouter(prefix="/api/v1/keys", tags=["keys"])
_settings = get_settings()


@router.get("", response_model=list[KeyOut])
async def list_keys(
    project_id: uuid.UUID | None = None,
    status_filter: KeyStatus | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> list[KeyOut]:
    keys = await KeyService(session).list_keys(
        project_id=project_id,
        status=status_filter.value if status_filter else None,
    )
    return [KeyOut.from_model(key) for key in keys]


@router.post("", response_model=KeyOut, status_code=status.HTTP_201_CREATED)
async def register_key(
    body: KeyCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.MANAGER)),
    validator: KeyValidator = Depends(get_key_validator),
) -> KeyOut:
    key = await KeyService(session, validator).register(
        name=body.name,
        api_key=body.api_key,
        owner=user,
        project_id=body.project_id,
        expires_at=body.expires_at,
        validate_remote=body.validate_remote,
        ip_address=client_ip(request),
    )
    return KeyOut.from_model(key)


@router.get("/dispense", response_model=DispensedKey)
@limiter.limit(_settings.rate_limit_dispense)
async def dispense_key(
    request: Request,
    project_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.MANAGER)),
) -> DispensedKey:
    """Return a ready-to-use key (least recently used, active, not expired)."""
    key, plaintext = await KeyService(session).dispense(
        project_id=project_id, actor=user, ip_address=client_ip(request)
    )
    return DispensedKey(
        key_id=key.id,
        name=key.name,
        key_hint=key.key_hint,
        project_id=key.project_id,
        api_key=plaintext,
    )


@router.post("/maintenance/expiry-check", response_model=list[KeyOut])
async def run_expiry_check(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_role(Role.MANAGER)),
) -> list[KeyOut]:
    expired = await KeyService(session).check_expirations()
    return [KeyOut.from_model(key) for key in expired]


@router.get("/{key_id}", response_model=KeyOut)
async def get_key(
    key_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> KeyOut:
    return KeyOut.from_model(await KeyService(session).get(key_id))


@router.post("/{key_id}/validate", response_model=KeyValidationOut)
async def validate_key(
    key_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.MANAGER)),
    validator: KeyValidator = Depends(get_key_validator),
) -> KeyValidationOut:
    key, outcome = await KeyService(session, validator).validate(
        key_id, actor=user, ip_address=client_ip(request)
    )
    return KeyValidationOut(
        key=KeyOut.from_model(key),
        result=outcome.result,
        status_code=outcome.status_code,
        detail=outcome.detail,
    )


@router.post("/{key_id}/rotate", response_model=KeyOut, status_code=status.HTTP_201_CREATED)
async def rotate_key(
    key_id: uuid.UUID,
    body: KeyRotate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.MANAGER)),
) -> KeyOut:
    """Assisted rotation: registers the new key and revokes the old one atomically."""
    replacement = await KeyService(session).rotate(
        key_id,
        new_api_key=body.api_key,
        actor=user,
        expires_at=body.expires_at,
        ip_address=client_ip(request),
    )
    return KeyOut.from_model(replacement)


@router.post("/{key_id}/revoke", response_model=KeyOut)
async def revoke_key(
    key_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.MANAGER)),
) -> KeyOut:
    key = await KeyService(session).revoke(key_id, actor=user, ip_address=client_ip(request))
    return KeyOut.from_model(key)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(
    key_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_role(Role.ADMIN)),
) -> None:
    await KeyService(session).delete(key_id, actor=admin, ip_address=client_ip(request))

"""Audit trail endpoints (admin only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.api.schemas import AuditOut
from app.application.services.audit_service import AuditService
from app.domain.enums import Role
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_session

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=list[AuditOut])
async def list_audit(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    action: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_role(Role.ADMIN)),
) -> list[AuditOut]:
    entries = await AuditService(session).list_entries(limit=limit, offset=offset, action=action)
    return [AuditOut.model_validate(entry) for entry in entries]

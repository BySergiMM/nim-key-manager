"""Statistics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.api.schemas import StatsOverview, UsagePoint
from app.application.services.stats_service import StatsService
from app.domain.enums import Role
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_session

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


@router.get("/overview", response_model=StatsOverview)
async def overview(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> StatsOverview:
    return StatsOverview(**await StatsService(session).overview())


@router.get("/usage", response_model=list[UsagePoint])
async def usage(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> list[UsagePoint]:
    points = await StatsService(session).usage_timeseries(days)
    return [UsagePoint(**point) for point in points]

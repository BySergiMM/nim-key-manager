"""Liveness/readiness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api.schemas import HealthOut
from app.core.config import get_settings
from app.infrastructure.db.session import get_session

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
async def health(session: AsyncSession = Depends(get_session)) -> HealthOut:
    try:
        await session.execute(text("SELECT 1"))
        database = "up"
    except Exception:  # pragma: no cover - only on infrastructure failure
        database = "down"
    settings = get_settings()
    return HealthOut(
        status="ok" if database == "up" else "degraded",
        database=database,
        version=__version__,
        environment=settings.environment,
    )

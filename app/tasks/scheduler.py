"""Background jobs: expiry detection and periodic remote validation."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.application.services.key_service import KeyService
from app.core.config import get_settings
from app.core.logging import get_logger
from app.infrastructure.db.session import SessionFactory
from app.infrastructure.nvidia.client import NvidiaKeyValidator

logger = get_logger(__name__)


async def run_expiry_check() -> None:
    async with SessionFactory() as session:
        expired = await KeyService(session).check_expirations()
        if expired:
            logger.info("keys_expired", count=len(expired), ids=[str(k.id) for k in expired])


async def run_validation_sweep() -> None:
    async with SessionFactory() as session:
        checked = await KeyService(session, NvidiaKeyValidator()).validate_all_active()
        logger.info("validation_sweep_completed", keys_checked=checked)


def build_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(run_expiry_check, "interval", hours=1, id="expiry_check")
    scheduler.add_job(
        run_validation_sweep,
        "interval",
        hours=settings.validation_interval_hours,
        id="validation_sweep",
    )
    return scheduler

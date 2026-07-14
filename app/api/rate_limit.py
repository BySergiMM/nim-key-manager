"""Application-wide rate limiter (slowapi, in-memory storage)."""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

_settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[_settings.rate_limit_default],
    enabled=_settings.rate_limit_enabled,
)

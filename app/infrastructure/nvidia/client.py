"""NVIDIA Build/NIM gateway.

NVIDIA Build does not expose an official public API for creating or rotating
API keys (keys are generated manually at build.nvidia.com/settings/api-keys).
The only official, ToS-compliant programmatic operation is *validation*:
calling the read-only ``/v1/models`` endpoint with the key. This module
implements exactly that and nothing else.
"""

from __future__ import annotations

import httpx

from app.application.interfaces import KeyValidationOutcome
from app.core.config import get_settings
from app.domain.enums import KeyCheckResult


class NvidiaKeyValidator:
    """Checks a key against NVIDIA's official, read-only models endpoint."""

    def __init__(self, url: str | None = None, timeout_seconds: float | None = None) -> None:
        settings = get_settings()
        self._url = url or settings.nvidia_validation_url
        self._timeout = timeout_seconds or settings.nvidia_request_timeout_seconds

    async def validate(self, api_key: str) -> KeyValidationOutcome:
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self._url, headers=headers)
        except httpx.HTTPError as exc:
            return KeyValidationOutcome(result=KeyCheckResult.UNREACHABLE, detail=str(exc))
        if response.status_code == 200:
            return KeyValidationOutcome(result=KeyCheckResult.VALID, status_code=200)
        if response.status_code in (401, 403):
            return KeyValidationOutcome(
                result=KeyCheckResult.INVALID,
                status_code=response.status_code,
                detail="NVIDIA rejected the key",
            )
        return KeyValidationOutcome(
            result=KeyCheckResult.UNREACHABLE,
            status_code=response.status_code,
            detail=response.text[:200],
        )

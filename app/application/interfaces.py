"""Application-layer ports (interfaces) implemented by infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.enums import KeyCheckResult


@dataclass(slots=True)
class KeyValidationOutcome:
    """Result of checking an API key against the provider."""

    result: KeyCheckResult
    status_code: int | None = None
    detail: str | None = None


class KeyValidator(Protocol):
    """Port for validating an API key against its provider."""

    async def validate(self, api_key: str) -> KeyValidationOutcome:  # pragma: no cover
        ...

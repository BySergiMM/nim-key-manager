"""Domain-level exceptions, mapped to HTTP status codes at the API boundary."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain errors."""


class NotFoundError(DomainError):
    """Requested resource does not exist."""


class ConflictError(DomainError):
    """Operation conflicts with current state (duplicates, invariants)."""


class PermissionDeniedError(DomainError):
    """Actor lacks the required role."""


class InvalidCredentialsError(DomainError):
    """Login failed."""


class InvalidTokenError(DomainError):
    """JWT is missing, malformed, expired or of the wrong type."""


class DecryptionError(DomainError):
    """Stored ciphertext could not be decrypted (wrong master key or tampering)."""


class NoKeyAvailableError(DomainError):
    """No active API key matches the dispense request."""


class ValidationFailedError(DomainError):
    """Input failed a business validation rule."""


class ConfigurationError(DomainError):
    """The application is misconfigured (missing or invalid settings)."""

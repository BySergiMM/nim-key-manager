"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core import paths
from app.domain.enums import Role

# Placeholders shipped as defaults so the package imports without configuration.
# They are refused outside development/test (see ``insecure_defaults``).
INSECURE_JWT_SECRET = "insecure-dev-secret-change-me"
INSECURE_MASTER_KEY = "insecure-dev-master-key-change-me"

_LOCAL_ENVIRONMENTS = frozenset({"development", "dev", "local", "test", "testing"})


class Settings(BaseSettings):
    """Central, typed application configuration.

    Values are resolved in this order (first match wins): process environment,
    a ``.env`` in the working directory (development), then the generated
    ``config.env`` in the install home (see :mod:`app.core.paths`). The last one
    is what ``nimkm init`` writes, so an installed CLI works from any directory.
    """

    model_config = SettingsConfigDict(
        # pydantic-settings gives precedence to the *last* file in the tuple.
        env_file=(paths.config_file(), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core
    app_name: str = "NIM Key Manager"
    environment: str = "production"
    debug: bool = False

    # HTTP server (``nimkm serve``). PORT is what PaaS providers inject.
    host: str = "127.0.0.1"
    port: int = 8000

    # Security
    jwt_secret: str = INSECURE_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    encryption_master_key: str = INSECURE_MASTER_KEY

    # Database. Defaults to SQLite inside the install home so the location does
    # not depend on the working directory the process was started from.
    database_url: str = Field(default_factory=paths.default_database_url)
    auto_create_tables: bool = True

    # Bootstrap admin (created on first startup when the user table is empty)
    first_admin_email: str | None = None
    first_admin_password: str | None = None

    # NVIDIA (official, read-only validation endpoint)
    nvidia_validation_url: str = "https://integrate.api.nvidia.com/v1/models"
    nvidia_request_timeout_seconds: float = 10.0

    # Observability
    # instrument() adds per-request Prometheus metrics; /metrics is always exposed.
    metrics_enabled: bool = True

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_default: str = "120/minute"
    rate_limit_auth: str = "10/minute"
    rate_limit_dispense: str = "60/minute"

    # Background jobs
    scheduler_enabled: bool = True
    validation_interval_hours: int = 6
    expiry_warning_days: int = 7

    # CORS
    cors_origins: list[str] = ["*"]

    # ------------------------------------------------------------------
    # MCP connector (Claude custom connector)
    # ------------------------------------------------------------------
    # Claude custom connectors speak OAuth 2.1 (Authorization Code + PKCE).
    # The MCP server is mounted at ``mcp_mount_path`` on the same service.
    mcp_enabled: bool = True
    mcp_auth_enabled: bool = True
    mcp_auth_provider: Literal["github", "google"] = "github"
    mcp_mount_path: str = "/mcp"

    # Public base URL clients use to reach this service. On Render it is taken
    # automatically from RENDER_EXTERNAL_URL when unset.
    public_base_url: str | None = None

    # OAuth application credentials (register the app with the provider).
    mcp_github_client_id: str | None = None
    mcp_github_client_secret: str | None = None
    mcp_google_client_id: str | None = None
    mcp_google_client_secret: str | None = None

    # Stable signing key for the JWTs FastMCP issues to clients (survives
    # restarts). Falls back to ``jwt_secret`` when unset.
    mcp_oauth_jwt_signing_key: str | None = None

    # Optional persistent OAuth client/token storage (Redis) so Claude does not
    # need to re-authorize after every restart/redeploy. In-memory when unset.
    mcp_redis_url: str | None = None
    mcp_storage_encryption_key: str | None = None

    # Identity allow-list: GitHub logins and/or e-mails permitted to use the
    # connector. Fail-closed: empty list denies everyone when auth is enabled.
    mcp_allowed_identities: Annotated[list[str], NoDecode] = []
    mcp_auto_provision: bool = True
    mcp_default_role: Role = Role.ADMIN

    # Host/Origin protection for the Streamable HTTP transport. Defaults derive
    # from ``public_base_url`` (plus loopback) when left empty.
    mcp_allowed_hosts: Annotated[list[str], NoDecode] = []
    mcp_allowed_origins: Annotated[list[str], NoDecode] = ["https://claude.ai"]

    # Dev-only principal used to resolve the actor when auth is disabled.
    mcp_dev_identity: str | None = None

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Normalize Heroku/Render style URLs to the asyncpg driver."""
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @field_validator(
        "first_admin_email",
        "first_admin_password",
        "public_base_url",
        "mcp_github_client_id",
        "mcp_github_client_secret",
        "mcp_google_client_id",
        "mcp_google_client_secret",
        "mcp_oauth_jwt_signing_key",
        "mcp_redis_url",
        "mcp_storage_encryption_key",
        "mcp_dev_identity",
        mode="before",
    )
    @classmethod
    def _empty_string_as_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator(
        "mcp_allowed_identities",
        "mcp_allowed_hosts",
        "mcp_allowed_origins",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept a comma-separated string as well as a JSON list."""
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                import json

                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    def resolve_public_base_url(self) -> str | None:
        """Public base URL, falling back to Render's injected external URL."""
        url = self.public_base_url or os.environ.get("RENDER_EXTERNAL_URL")
        return url.rstrip("/") if url else None

    # ------------------------------------------------------------------
    # Readiness helpers (used by startup checks and ``nimkm doctor``)
    # ------------------------------------------------------------------
    @property
    def is_local_environment(self) -> bool:
        return self.environment.strip().lower() in _LOCAL_ENVIRONMENTS

    def insecure_defaults(self) -> list[str]:
        """Names of secrets still set to the shipped placeholder values."""
        placeholders = {
            "JWT_SECRET": (self.jwt_secret, INSECURE_JWT_SECRET),
            "ENCRYPTION_MASTER_KEY": (self.encryption_master_key, INSECURE_MASTER_KEY),
        }
        return [name for name, (value, default) in placeholders.items() if value == default]

    def require_secure_secrets(self) -> None:
        """Refuse to start outside development with the shipped placeholders.

        API keys are encrypted with a value derived from
        ``ENCRYPTION_MASTER_KEY``; booting with the public default would make the
        stored ciphertext trivially decryptable, so this fails closed.
        """
        if self.is_local_environment:
            return
        missing = self.insecure_defaults()
        if missing:
            from app.domain.exceptions import ConfigurationError

            raise ConfigurationError(
                f"{' and '.join(missing)} still use the built-in development "
                "placeholder. Run 'nimkm init' to generate real secrets, or set "
                "them in the environment (ENVIRONMENT=development skips this check)."
            )

    def mcp_credentials_configured(self) -> bool:
        """True when OAuth credentials for the selected provider are present."""
        if self.mcp_auth_provider == "google":
            return bool(self.mcp_google_client_id and self.mcp_google_client_secret)
        return bool(self.mcp_github_client_id and self.mcp_github_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()

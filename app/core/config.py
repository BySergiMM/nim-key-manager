"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.domain.enums import Role
from app.domain.exceptions import ConfigurationError

# Placeholders shipped as defaults so that the package imports without configuration.
# They are refused outside local development (see ``Settings.require_secure_secrets``).
INSECURE_JWT_SECRET = "insecure-dev-secret-change-me"
INSECURE_MASTER_KEY = "insecure-dev-master-key-change-me"

# RFC 7518 section 3.2: an HS256 key must be at least as long as the hash output (256 bits).
# The master key feeds an HKDF-SHA256 derivation, so the same floor applies to it.
MIN_SECRET_LENGTH = 32

# Environments in which the placeholder check is skipped. Anything else, including a value
# nobody recognises, is treated as production.
_LOCAL_ENVIRONMENTS = frozenset({"development", "dev", "local", "test", "testing"})

# Text that marks a value as copied from .env.example, docker-compose.yml or the docs.
_PLACEHOLDER_MARKERS = ("change-me", "changeme", "insecure-dev")


class Settings(BaseSettings):
    """Central, typed application configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    app_name: str = "NIM Key Manager"
    environment: str = "production"
    debug: bool = False

    # Security
    jwt_secret: str = INSECURE_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    encryption_master_key: str = INSECURE_MASTER_KEY

    # Database
    database_url: str = "sqlite+aiosqlite:///./local.db"
    auto_create_tables: bool = True

    # Bootstrap admin (created on first startup when the user table is empty)
    first_admin_email: str | None = None
    first_admin_password: str | None = None

    # NVIDIA (official, read-only validation endpoint)
    nvidia_validation_url: str = "https://integrate.api.nvidia.com/v1/models"
    nvidia_request_timeout_seconds: float = 10.0

    # Observability
    # instrument() adds per-request Prometheus metrics. /metrics is always exposed but needs
    # an administrator's access token or, for a scraper, this bearer token (unset = admin only).
    metrics_enabled: bool = True
    metrics_token: str | None = None

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_default: str = "120/minute"
    rate_limit_auth: str = "10/minute"
    rate_limit_dispense: str = "60/minute"

    # Background jobs
    scheduler_enabled: bool = True
    validation_interval_hours: int = 6
    expiry_warning_days: int = 7

    # CORS: origins (JSON list) allowed to call the API from a browser. None by default: the
    # dashboard is served by this same service and needs no CORS. "*" is accepted but is
    # never combined with credentials (see create_app).
    cors_origins: list[str] = []

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

    # Identity allow-list: stable account ids (GitHub's numeric user id, Google's ``sub``)
    # and/or e-mail addresses permitted to use the connector. GitHub logins are not accepted:
    # they can be renamed and re-registered by somebody else. Fail-closed: an empty list
    # denies everyone when auth is enabled.
    mcp_allowed_identities: Annotated[list[str], NoDecode] = []
    mcp_auto_provision: bool = True
    # Role of a user created on first connection. Least privilege by default: a viewer cannot
    # dispense, register or delete keys. Raise it deliberately (MCP_DEFAULT_ROLE) or promote
    # the user through the API (PATCH /api/v1/users/{id}).
    mcp_default_role: Role = Role.VIEWER

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
        "metrics_token",
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

    # ------------------------------------------------------------------
    # Startup checks
    # ------------------------------------------------------------------
    @property
    def is_local_environment(self) -> bool:
        return self.environment.strip().lower() in _LOCAL_ENVIRONMENTS

    def secret_problems(self) -> list[str]:
        """What is wrong with the configured secrets, one sentence per problem.

        Names the variable and the problem; never the value.
        """
        problems: list[str] = []

        def check(name: str, value: str | None, *, minimum: int) -> None:
            text = (value or "").strip()
            if not text:
                problems.append(f"{name} is empty")
            elif is_placeholder(text):
                problems.append(f"{name} is a placeholder from the example configuration")
            elif len(text) < minimum:
                problems.append(f"{name} is shorter than {minimum} characters")

        check("JWT_SECRET", self.jwt_secret, minimum=MIN_SECRET_LENGTH)
        check("ENCRYPTION_MASTER_KEY", self.encryption_master_key, minimum=MIN_SECRET_LENGTH)
        # Optional ones are only checked when set: unset means "derive from JWT_SECRET" or
        # "feature off", which is safe.
        if self.mcp_oauth_jwt_signing_key is not None:
            check(
                "MCP_OAUTH_JWT_SIGNING_KEY",
                self.mcp_oauth_jwt_signing_key,
                minimum=MIN_SECRET_LENGTH,
            )
        if self.metrics_token is not None:
            check("METRICS_TOKEN", self.metrics_token, minimum=MIN_SECRET_LENGTH)
        if self.first_admin_password is not None and is_placeholder(self.first_admin_password):
            problems.append("FIRST_ADMIN_PASSWORD is a placeholder from the example configuration")
        return problems

    def require_secure_secrets(self) -> None:
        """Refuse to start outside local development with placeholder, empty or short secrets.

        API keys are encrypted with a value derived from ``ENCRYPTION_MASTER_KEY`` and every
        session is a JWT signed with ``JWT_SECRET``. Starting with the public placeholder would
        make the stored ciphertext decryptable, and the tokens forgeable, by anyone who has read
        this repository.
        """
        if self.is_local_environment:
            return
        problems = self.secret_problems()
        if problems:
            raise ConfigurationError(
                f"Refusing to start with ENVIRONMENT={self.environment}: "
                + "; ".join(problems)
                + f". Use your own random values of at least {MIN_SECRET_LENGTH} characters "
                "(for example: openssl rand -base64 48); FIRST_ADMIN_PASSWORD, if set, must be "
                "a password of your own. ENVIRONMENT=development skips this check and is for "
                "local use only."
            )

    def mcp_credentials_configured(self) -> bool:
        """True when the OAuth client credentials of the selected provider are both set."""
        if self.mcp_auth_provider == "google":
            return bool(self.mcp_google_client_id and self.mcp_google_client_secret)
        return bool(self.mcp_github_client_id and self.mcp_github_client_secret)

    def resolve_public_base_url(self) -> str | None:
        """Public base URL, falling back to Render's injected external URL."""
        url = self.public_base_url or os.environ.get("RENDER_EXTERNAL_URL")
        return url.rstrip("/") if url else None


def is_placeholder(value: str) -> bool:
    """True for the values this repository ships as examples (not for real secrets)."""
    normalized = value.strip().lower()
    return any(marker in normalized for marker in _PLACEHOLDER_MARKERS)


@lru_cache
def get_settings() -> Settings:
    return Settings()

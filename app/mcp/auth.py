"""OAuth 2.1 provider factory for the Claude custom connector.

Claude custom connectors authenticate with OAuth 2.1 (Authorization Code + PKCE)
and expect Dynamic Client Registration. GitHub and Google do not support DCR, so
FastMCP's provider classes wrap them with an OAuth Proxy that presents a
DCR-compliant facade to Claude while using our pre-registered app credentials
upstream. FastMCP issues its own short-lived JWTs to Claude and never forwards
the upstream provider token.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.exceptions import ConfigurationError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastmcp.server.auth import AuthProvider
    from key_value.aio.protocols import AsyncKeyValue

logger = get_logger(__name__)

# Redirect URIs Claude uses for the OAuth callback. Whitelisted so the OAuth
# proxy accepts Claude's dynamic client callback.
CLAUDE_REDIRECT_URIS: list[str] = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
]


def build_auth_provider(settings: Settings, base_url: str) -> AuthProvider | None:
    """Build the OAuth provider for the MCP server, or ``None`` when disabled.

    Returning ``None`` leaves the MCP endpoint unauthenticated and must only be
    used for local development/testing (``MCP_AUTH_ENABLED=false``).
    """
    if not settings.mcp_auth_enabled:
        logger.warning("mcp_auth_disabled", detail="MCP endpoint is UNAUTHENTICATED")
        return None

    signing_key = settings.mcp_oauth_jwt_signing_key or settings.jwt_secret
    client_storage = _build_client_storage(settings)
    provider = settings.mcp_auth_provider

    if provider == "github":
        from fastmcp.server.auth.providers.github import GitHubProvider

        client_id, client_secret = _require_credentials(
            settings.mcp_github_client_id, settings.mcp_github_client_secret, "GitHub"
        )
        return GitHubProvider(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            required_scopes=["read:user", "user:email"],
            allowed_client_redirect_uris=CLAUDE_REDIRECT_URIS,
            jwt_signing_key=signing_key,
            client_storage=client_storage,
        )

    if provider == "google":
        from fastmcp.server.auth.providers.google import GoogleProvider

        client_id, client_secret = _require_credentials(
            settings.mcp_google_client_id, settings.mcp_google_client_secret, "Google"
        )
        return GoogleProvider(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            required_scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
            ],
            allowed_client_redirect_uris=CLAUDE_REDIRECT_URIS,
            jwt_signing_key=signing_key,
            client_storage=client_storage,
        )

    raise ConfigurationError(f"unsupported MCP auth provider: {provider!r}")


def _require_credentials(
    client_id: str | None, client_secret: str | None, provider: str
) -> tuple[str, str]:
    if not client_id or not client_secret:
        raise ConfigurationError(
            f"{provider} OAuth is enabled but MCP_{provider.upper()}_CLIENT_ID / "
            f"MCP_{provider.upper()}_CLIENT_SECRET are not set"
        )
    return client_id, client_secret


def _build_client_storage(settings: Settings) -> AsyncKeyValue | None:
    """Persistent, encrypted OAuth client/token storage (optional).

    Without it FastMCP keeps clients/tokens in memory, so Claude must
    re-authorize after every restart. Enabling ``MCP_REDIS_URL`` (plus a Fernet
    ``MCP_STORAGE_ENCRYPTION_KEY``) makes authorizations survive redeploys.
    """
    if not settings.mcp_redis_url:
        return None
    try:
        from key_value.aio.stores.redis import RedisStore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ConfigurationError(
            "MCP_REDIS_URL is set but the Redis store is not installed; "
            "add 'py-key-value-aio[redis]' to enable persistent OAuth storage"
        ) from exc

    store: AsyncKeyValue = RedisStore(url=settings.mcp_redis_url)
    if settings.mcp_storage_encryption_key:
        from cryptography.fernet import Fernet
        from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

        store = FernetEncryptionWrapper(
            key_value=store, fernet=Fernet(settings.mcp_storage_encryption_key)
        )
    else:  # pragma: no cover - defensive warning path
        logger.warning("mcp_storage_unencrypted", detail="set MCP_STORAGE_ENCRYPTION_KEY")
    return store

"""Compose the MCP connector and the REST/dashboard app into one ASGI service.

The FastMCP HTTP app keeps its own middleware stack (OAuth authentication, host
protection) so the ``/mcp`` endpoint stays protected and the OAuth discovery
routes live at the domain root. The existing FastAPI application is attached as a
catch-all mount, so ``/``, ``/api/v1/*``, ``/health`` and ``/metrics`` are served
unchanged: the MCP layer adds no authentication of its own to them, and each keeps the
access rules of the FastAPI app (``/metrics`` needs ``METRICS_TOKEN`` or an administrator).
Both lifespans run via ``combine_lifespans`` (MCP session manager + database/scheduler
startup).
"""

from __future__ import annotations

from urllib.parse import urlparse

from starlette.applications import Starlette
from starlette.routing import Mount

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.exceptions import ConfigurationError
from app.mcp.identity import parse_allow_list

logger = get_logger(__name__)

_LOCAL_FALLBACK_BASE_URL = "http://localhost:8000"


def _allowed_hosts(settings: Settings, base_url: str) -> list[str]:
    if settings.mcp_allowed_hosts:
        return list(settings.mcp_allowed_hosts)
    hosts: list[str] = []
    hostname = urlparse(base_url).hostname
    if hostname:
        hosts.append(hostname)
    hosts += ["localhost", "127.0.0.1"]
    return hosts


def _report_allow_list(settings: Settings) -> None:
    """Say at start-up why nobody (or not everybody) can connect, instead of failing later."""
    if not settings.mcp_auth_enabled:
        return
    allow = parse_allow_list(settings.mcp_allowed_identities)
    if allow.ignored:
        logger.warning(
            "mcp_allow_list_entries_ignored",
            entries=list(allow.ignored),
            detail=(
                "MCP_ALLOWED_IDENTITIES takes numeric account ids (GitHub user id, Google sub) "
                "or e-mail addresses; a GitHub login is not accepted because it can be renamed "
                "and then registered by somebody else "
                "(see docs/connector.md#migrating-an-existing-allow-list)"
            ),
        )
    if not (allow.ids or allow.emails):
        logger.warning(
            "mcp_allow_list_empty",
            detail="MCP_ALLOWED_IDENTITIES has no usable entry, so nobody can use the connector",
        )


def mount_mcp_connector(api_app: Starlette, settings: Settings) -> Starlette:
    """Return the composed ASGI app, or ``api_app`` unchanged when MCP is off.

    The connector is optional. An instance that has not been given OAuth credentials yet (the
    Render blueprint declares them ``sync: false``, so they may be left blank) serves the REST API
    and the dashboard normally, leaves ``/mcp`` off and says so in a warning. Only a connector
    that is half configured (credentials but no public URL) is a fatal misconfiguration.
    """
    if not settings.mcp_enabled:
        logger.info("mcp_connector_disabled")
        return api_app

    if settings.mcp_auth_enabled and not settings.mcp_credentials_configured():
        provider = settings.mcp_auth_provider.upper()
        logger.warning(
            "mcp_connector_not_configured",
            detail=(
                f"/mcp is OFF: set MCP_{provider}_CLIENT_ID and MCP_{provider}_CLIENT_SECRET "
                "(and MCP_ALLOWED_IDENTITIES) to enable the Claude connector; the REST API and "
                "the dashboard are running normally (see docs/connector.md)"
            ),
        )
        return api_app

    _report_allow_list(settings)

    base_url = settings.resolve_public_base_url()
    if base_url is None:
        if settings.mcp_auth_enabled:
            raise ConfigurationError(
                "PUBLIC_BASE_URL (or RENDER_EXTERNAL_URL) is required to run the "
                "OAuth-secured MCP connector; set it or disable MCP_AUTH_ENABLED"
            )
        base_url = _LOCAL_FALLBACK_BASE_URL

    # Imported lazily so the REST app and tests do not require fastmcp at import
    # time unless the connector is actually enabled.
    from fastmcp.utilities.lifespan import combine_lifespans

    from app.mcp.server import build_mcp_server

    mcp = build_mcp_server(settings, base_url)
    mcp_app = mcp.http_app(
        path=settings.mcp_mount_path,
        allowed_hosts=_allowed_hosts(settings, base_url),
        allowed_origins=settings.mcp_allowed_origins or None,
    )

    mcp_lifespan = mcp_app.lifespan
    mcp_app.router.routes.append(Mount("/", app=api_app))
    mcp_app.router.lifespan_context = combine_lifespans(
        mcp_lifespan, api_app.router.lifespan_context
    )

    logger.info(
        "mcp_connector_mounted",
        endpoint=f"{base_url}{settings.mcp_mount_path}",
        provider=settings.mcp_auth_provider if settings.mcp_auth_enabled else "none",
        authenticated=settings.mcp_auth_enabled,
    )
    return mcp_app

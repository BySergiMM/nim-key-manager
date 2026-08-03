"""Run the MCP server over stdio, the transport local clients speak.

This is the primary entry point: an MCP client (Claude Code, Claude Desktop, any
other) spawns ``nimkm mcp serve`` as a child process and exchanges JSON-RPC over
its standard input and output.

Two consequences drive everything in this module:

* **stdout belongs to the protocol.** A single stray ``print`` corrupts the
  session, so all logging is redirected to stderr before anything is imported
  that might log.
* **The transport is the trust boundary.** There is no OAuth handshake to
  identify the caller; whoever can spawn this process already has the user's
  file permissions, so tools run as a local administrator account. Remote
  access still goes through the OAuth-protected HTTP connector.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastmcp import FastMCP

# ``.internal`` is the TLD IANA reserved for private networks, so this address is
# guaranteed never to resolve. ``.local``/``.localhost`` are rejected outright by
# e-mail validation, and the value has to survive ``UserOut``.
LOCAL_IDENTITY_EMAIL = "local@nimkm.internal"
LOCAL_IDENTITY_NAME = "Local MCP client"


def prepare_environment(identity: str | None = None) -> None:
    """Force the local, unauthenticated profile before settings are first read.

    ``Settings`` is cached on first use and the MCP tool layer reads it through
    ``get_settings()``, so the profile has to be established through the
    environment *before* any application import happens.
    """
    from app.mcp.auth import STDIO_TRANSPORT_ENV

    os.environ[STDIO_TRANSPORT_ENV] = "stdio"
    os.environ["MCP_AUTH_ENABLED"] = "false"
    os.environ["MCP_ENABLED"] = "true"
    os.environ["SCHEDULER_ENABLED"] = "false"  # a client-spawned process is short-lived
    os.environ["MCP_DEV_IDENTITY"] = identity or LOCAL_IDENTITY_EMAIL


async def ensure_local_identity() -> str:
    """Guarantee an administrator the stdio session can act as.

    A fresh install has no users at all. Rather than failing with "no active
    admin", provision a dedicated local account: it owns no password anyone
    knows, and the dashboard has its own accounts created by ``nimkm init``.
    """
    import secrets

    from app.core.security import hash_password
    from app.domain.enums import Role
    from app.infrastructure.db.models import User
    from app.infrastructure.db.repositories import UserRepository
    from app.infrastructure.db.session import SessionFactory

    async with SessionFactory() as session:
        users = UserRepository(session)
        existing = await users.get_by_email(LOCAL_IDENTITY_EMAIL)
        if existing is not None:
            return existing.email
        for candidate in await users.list_all():
            if candidate.role == Role.ADMIN.value and candidate.is_active:
                return candidate.email
        user = User(
            email=LOCAL_IDENTITY_EMAIL,
            hashed_password=hash_password(secrets.token_urlsafe(32)),
            full_name=LOCAL_IDENTITY_NAME,
            role=Role.ADMIN.value,
        )
        await users.add(user)
        await session.commit()
        return user.email


def build_server() -> FastMCP:
    """The same tool surface the HTTP connector exposes, without OAuth."""
    from app.core.config import get_settings
    from app.mcp.server import build_mcp_server

    settings = get_settings()
    # base_url is only used to advertise OAuth metadata, which stdio has none of.
    return build_mcp_server(settings, "stdio://local")


def run(identity: str | None = None) -> int:
    """Serve MCP over stdio until the client closes the connection."""
    import asyncio
    import logging

    from app.core.logging import configure_logging

    # Everything human-readable goes to stderr; stdout is the JSON-RPC channel.
    configure_logging(stream=sys.stderr)
    logging.getLogger("uvicorn").handlers.clear()

    from app.infrastructure.db import migrations

    # Only pays for Alembic when the database is actually behind: this runs on
    # every single client spawn.
    migrations.upgrade_if_needed()
    asyncio.run(ensure_local_identity())

    import contextlib

    server = build_server()
    # The client closing the pipe is the normal way this process ends.
    with contextlib.suppress(KeyboardInterrupt, asyncio.CancelledError):
        asyncio.run(server.run_stdio_async(show_banner=False))
    return 0

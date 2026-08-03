"""The stdio transport: what an MCP client actually spawns.

The end-to-end test drives a real client over a real pipe, because the failure
modes that matter here (a stray byte on stdout, a missing database, no usable
identity) only appear in a separate process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from app.mcp import stdio

REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_ONLY_ENV = (
    "DATABASE_URL", "JWT_SECRET", "ENCRYPTION_MASTER_KEY", "AUTO_CREATE_TABLES",
    "ENVIRONMENT", "DEBUG", "SCHEDULER_ENABLED", "RATE_LIMIT_ENABLED",
    "METRICS_ENABLED", "FIRST_ADMIN_EMAIL", "FIRST_ADMIN_PASSWORD",
    "MCP_AUTH_ENABLED", "MCP_DEV_IDENTITY",
)


def test_prepare_environment_selects_the_local_profile(monkeypatch) -> None:
    for name in ("MCP_AUTH_ENABLED", "SCHEDULER_ENABLED", "MCP_DEV_IDENTITY"):
        monkeypatch.delenv(name, raising=False)
    stdio.prepare_environment()
    assert os.environ["MCP_AUTH_ENABLED"] == "false"
    assert os.environ["MCP_ENABLED"] == "true"
    # A client-spawned process must not start background jobs.
    assert os.environ["SCHEDULER_ENABLED"] == "false"
    assert os.environ["MCP_DEV_IDENTITY"] == stdio.LOCAL_IDENTITY_EMAIL
    from app.mcp.auth import STDIO_TRANSPORT_ENV

    assert os.environ[STDIO_TRANSPORT_ENV] == "stdio"


def test_prepare_environment_accepts_an_explicit_identity(monkeypatch) -> None:
    stdio.prepare_environment("someone@example.com")
    assert os.environ["MCP_DEV_IDENTITY"] == "someone@example.com"


# --------------------------------------------------------------------------- #
# migrations: the cost every spawn pays                                        #
# --------------------------------------------------------------------------- #
def test_expected_head_matches_the_scripts() -> None:
    """Guards the constant that lets a warm start skip Alembic entirely.

    If this fails you added a migration: bump EXPECTED_HEAD in the same commit.
    """
    from app.infrastructure.db import migrations

    assert migrations.head_revision() == migrations.EXPECTED_HEAD


def test_upgrade_is_skipped_when_the_database_is_current(monkeypatch) -> None:
    from app.infrastructure.db import migrations

    ran = {"upgrade": False}
    monkeypatch.setattr(migrations, "stamped_revision", lambda: migrations.EXPECTED_HEAD)
    monkeypatch.setattr(migrations, "upgrade", lambda *a, **k: ran.update(upgrade=True))
    assert migrations.upgrade_if_needed() is False
    assert ran["upgrade"] is False


@pytest.mark.parametrize("stamped", [None, "0000", "unexpected"])
def test_upgrade_runs_whenever_there_is_any_doubt(monkeypatch, stamped) -> None:
    from app.infrastructure.db import migrations

    ran = {"upgrade": False}
    monkeypatch.setattr(migrations, "stamped_revision", lambda: stamped)
    monkeypatch.setattr(migrations, "upgrade", lambda *a, **k: ran.update(upgrade=True))
    assert migrations.upgrade_if_needed() is True
    assert ran["upgrade"] is True


def test_stamped_revision_survives_an_unreachable_database(monkeypatch) -> None:
    from app.infrastructure.db import migrations

    monkeypatch.setattr(
        migrations, "stamped_revision", migrations.stamped_revision
    )  # keep the real one
    import app.infrastructure.db.session as session_mod

    class Exploding:
        def connect(self):
            raise RuntimeError("no database here")

    monkeypatch.setattr(session_mod, "engine", Exploding())
    assert migrations.stamped_revision() is None


def test_local_identity_is_a_valid_address() -> None:
    """It has to survive UserOut; .local/.localhost/.invalid do not."""
    from pydantic import BaseModel, EmailStr

    class Probe(BaseModel):
        email: EmailStr

    assert Probe(email=stdio.LOCAL_IDENTITY_EMAIL).email == stdio.LOCAL_IDENTITY_EMAIL


async def test_ensure_local_identity_creates_then_reuses(empty_db) -> None:
    first = await stdio.ensure_local_identity()
    assert first == stdio.LOCAL_IDENTITY_EMAIL
    assert await stdio.ensure_local_identity() == first


async def test_ensure_local_identity_prefers_an_existing_admin(empty_db) -> None:
    from app.application.services.auth_service import AuthService
    from app.domain.enums import Role
    from app.infrastructure.db.session import SessionFactory

    async with SessionFactory() as session:
        await AuthService(session).register(
            email="owner@example.com", password="SuperSecret123",
            full_name="Owner", role=Role.ADMIN, actor=None,
        )
    assert await stdio.ensure_local_identity() == "owner@example.com"


@pytest.fixture
async def empty_db():
    from app.infrastructure.db.models import Base
    from app.infrastructure.db.session import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


# --------------------------------------------------------------------------- #
# end-to-end over a real pipe                                                  #
# --------------------------------------------------------------------------- #
def _clean_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _TEST_ONLY_ENV}
    env.update(NIMKM_HOME=str(home), PYTHONPATH=str(REPO_ROOT), NO_COLOR="1")
    return env


@pytest.mark.slow
@pytest.mark.parametrize("initialised", [False, True], ids=["cold", "after-init"])
async def test_a_client_can_drive_the_server_over_stdio(initialised) -> None:
    """Both entry orders must work.

    ``after-init`` is what a real installation does: ``nimkm init`` creates the
    bootstrap administrator first, and the stdio session then acts as that
    account. A previous version shipped an administrator address that failed
    serialisation, so every tool call broke on exactly this path.
    """
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    home = Path(tempfile.mkdtemp(prefix="nimkm-stdio-"))
    env = _clean_env(home)

    if initialised:
        created = subprocess.run(
            [sys.executable, "-m", "app.cli", "init", "--no-input"],
            env=env, capture_output=True, text=True, timeout=300, cwd=str(REPO_ROOT),
        )
        assert created.returncode == 0, created.stderr or created.stdout

    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "app.cli", "mcp", "serve"],
        env=env,
        cwd=str(REPO_ROOT),
    )

    async with Client(transport) as client:
        # A cold start with no configuration must still come up.
        tools = {tool.name for tool in await client.list_tools()}
        assert {"dispense_key", "register_key", "rotate_key", "whoami"} <= tools

        # Serialising the acting user is what broke before: assert it round-trips.
        who = await client.call_tool("whoami", {})
        assert who.data.role == "admin"
        assert who.data.email == (
            "admin@nimkm.internal" if initialised else stdio.LOCAL_IDENTITY_EMAIL
        )

        secret = "nvapi-" + uuid.uuid4().hex
        registered = await client.call_tool(
            "register_key",
            {"name": "from-mcp", "api_key": secret, "validate_remote": False},
        )
        assert registered.data.status == "active"

        dispensed = await client.call_tool("dispense_key", {})
        assert dispensed.data.api_key == secret

        overview = await client.call_tool("stats_overview", {})
        assert overview.data.total_keys == 1

    # The session provisioned a complete installation on its own.
    assert (home / "config.env").is_file()
    assert (home / "data" / "nimkm.db").is_file()


@pytest.mark.slow
def test_stdout_carries_protocol_and_nothing_else(tmp_path) -> None:
    """One stray print would corrupt every session, so assert on raw bytes.

    Uses the process directly rather than a client library: the point is what
    lands on the pipe, including anything printed during start-up.
    """
    home = tmp_path / "raw"
    env = _clean_env(home)

    request = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"}}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
    ]) + "\n"

    finished = subprocess.run(
        [sys.executable, "-m", "app.cli", "mcp", "serve"],
        input=request, capture_output=True, text=True, timeout=300,
        env=env, cwd=str(REPO_ROOT),
    )

    lines = [line for line in finished.stdout.splitlines() if line.strip()]
    assert lines, f"no protocol output at all; stderr was:\n{finished.stderr[-2000:]}"

    messages = []
    for line in lines:
        message = json.loads(line)          # raises if anything else was printed
        assert message["jsonrpc"] == "2.0"
        messages.append(message)

    handshake = next(m for m in messages if m.get("id") == 1)
    assert handshake["result"]["serverInfo"]["name"]
    # tools/list may or may not be flushed before the closed pipe ends the
    # process; when it is, it must be the real tool list.
    listing = next((m for m in messages if m.get("id") == 2), None)
    if listing is not None:
        assert any(t["name"] == "dispense_key" for t in listing["result"]["tools"])

    # Migrations, warnings and banners all belong on the other stream.
    assert "Running upgrade" in finished.stderr or "mcp_stdio_local" in finished.stderr

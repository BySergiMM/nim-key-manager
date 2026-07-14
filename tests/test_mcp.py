"""Tests for the MCP connector layer (auth factory, identity mapping, tools)."""

from __future__ import annotations

import uuid

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import app.mcp.server as server_mod
from app.application.interfaces import KeyValidationOutcome
from app.application.services.auth_service import AuthService
from app.core.config import get_settings
from app.domain.enums import KeyCheckResult, Role
from app.domain.exceptions import ConfigurationError, PermissionDeniedError
from app.infrastructure.db.models import Base
from app.infrastructure.db.repositories import UserRepository
from app.infrastructure.db.session import SessionFactory, engine
from app.mcp import identity as identity_mod
from app.mcp.asgi import mount_mcp_connector
from app.mcp.auth import build_auth_provider
from app.mcp.identity import Identity, is_allowed, resolve_actor, resolve_email

ADMIN_EMAIL = "admin@example.com"
VIEWER_EMAIL = "viewer@example.com"
BASE_URL = "http://localhost:8000"


class _FakeValidator:
    def __init__(self, result: KeyCheckResult = KeyCheckResult.VALID) -> None:
        self.result = result

    async def validate(self, api_key: str) -> KeyValidationOutcome:
        code = {KeyCheckResult.VALID: 200, KeyCheckResult.INVALID: 401}.get(self.result)
        return KeyValidationOutcome(result=self.result, status_code=code)


def _nvapi() -> str:
    return "nvapi-" + uuid.uuid4().hex


async def _reset_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def _seed_admin() -> None:
    async with SessionFactory() as session:
        await AuthService(session).register(
            email=ADMIN_EMAIL, password="SuperSecret123", full_name="Admin",
            role=Role.ADMIN, actor=None,
        )


async def _seed_viewer() -> None:
    async with SessionFactory() as session:
        admin = await UserRepository(session).get_by_email(ADMIN_EMAIL)
        await AuthService(session).register(
            email=VIEWER_EMAIL, password="Password123!", full_name="Viewer",
            role=Role.VIEWER, actor=admin,
        )


@pytest.fixture
async def mcp_client(monkeypatch):
    """In-memory MCP client acting as the admin dev identity (auth disabled)."""
    await _reset_db()
    await _seed_admin()
    settings = get_settings()
    monkeypatch.setattr(settings, "mcp_auth_enabled", False)
    monkeypatch.setattr(settings, "mcp_dev_identity", ADMIN_EMAIL)
    monkeypatch.setattr(server_mod, "NvidiaKeyValidator", lambda *a, **k: _FakeValidator())
    server = server_mod.build_mcp_server(settings, BASE_URL)
    async with Client(server) as client:
        yield client


# --------------------------------------------------------------------------- #
# identity helpers                                                             #
# --------------------------------------------------------------------------- #
def test_resolve_email_prefers_email() -> None:
    assert resolve_email(Identity("1", "octo", "Octo@EX.com", "O")) == "octo@ex.com"


def test_resolve_email_falls_back_to_login_then_subject() -> None:
    assert resolve_email(Identity("1", "Octo", None, None)) == "octo@users.noreply.github.com"
    assert resolve_email(Identity("sub-9", None, None, None)) == "sub-9@mcp.local"


def test_is_allowed_matrix() -> None:
    s = get_settings().model_copy(update={"mcp_allowed_identities": ["octocat", "me@ex.com"]})
    assert is_allowed(Identity("1", "octocat", None, None), s) is True
    assert is_allowed(Identity("1", None, "me@ex.com", None), s) is True
    assert is_allowed(Identity("1", "stranger", "x@y.z", None), s) is False
    empty = get_settings().model_copy(update={"mcp_allowed_identities": []})
    assert is_allowed(Identity("1", "octocat", None, None), empty) is False


# --------------------------------------------------------------------------- #
# resolve_actor (auth enabled, identity injected)                             #
# --------------------------------------------------------------------------- #
async def test_resolve_actor_auto_provisions_allowed_identity(monkeypatch) -> None:
    await _reset_db()
    await _seed_admin()
    monkeypatch.setattr(identity_mod, "extract_identity",
                        lambda: Identity("42", "octocat", "octo@ex.com", "Octo"))
    settings = get_settings().model_copy(update={
        "mcp_auth_enabled": True, "mcp_allowed_identities": ["octocat"],
        "mcp_auto_provision": True, "mcp_default_role": Role.MANAGER,
    })
    async with SessionFactory() as session:
        actor = await resolve_actor(session, settings)
        assert actor.email == "octo@ex.com"
        assert actor.role == Role.MANAGER.value


async def test_resolve_actor_returns_existing_user(monkeypatch) -> None:
    await _reset_db()
    await _seed_admin()
    monkeypatch.setattr(identity_mod, "extract_identity",
                        lambda: Identity("1", None, ADMIN_EMAIL, "Admin"))
    settings = get_settings().model_copy(update={
        "mcp_auth_enabled": True, "mcp_allowed_identities": [ADMIN_EMAIL],
    })
    async with SessionFactory() as session:
        actor = await resolve_actor(session, settings)
        assert actor.email == ADMIN_EMAIL and actor.role == Role.ADMIN.value


async def test_resolve_actor_rejects_unlisted_identity(monkeypatch) -> None:
    await _reset_db()
    await _seed_admin()
    monkeypatch.setattr(identity_mod, "extract_identity",
                        lambda: Identity("9", "stranger", "s@ex.com", None))
    settings = get_settings().model_copy(update={
        "mcp_auth_enabled": True, "mcp_allowed_identities": ["octocat"],
    })
    async with SessionFactory() as session:
        with pytest.raises(PermissionDeniedError):
            await resolve_actor(session, settings)


async def test_resolve_actor_no_autoprovision_denies(monkeypatch) -> None:
    await _reset_db()
    await _seed_admin()
    monkeypatch.setattr(identity_mod, "extract_identity",
                        lambda: Identity("7", "octocat", "octo@ex.com", None))
    settings = get_settings().model_copy(update={
        "mcp_auth_enabled": True, "mcp_allowed_identities": ["octocat"],
        "mcp_auto_provision": False,
    })
    async with SessionFactory() as session:
        with pytest.raises(PermissionDeniedError):
            await resolve_actor(session, settings)


async def test_resolve_actor_dev_falls_back_to_first_admin() -> None:
    await _reset_db()
    await _seed_admin()
    settings = get_settings().model_copy(update={
        "mcp_auth_enabled": False, "mcp_dev_identity": None,
    })
    async with SessionFactory() as session:
        actor = await resolve_actor(session, settings)
        assert actor.role == Role.ADMIN.value


# --------------------------------------------------------------------------- #
# auth provider factory                                                        #
# --------------------------------------------------------------------------- #
def test_build_auth_provider_disabled_returns_none() -> None:
    s = get_settings().model_copy(update={"mcp_auth_enabled": False})
    assert build_auth_provider(s, BASE_URL) is None


def test_build_auth_provider_github_requires_credentials() -> None:
    s = get_settings().model_copy(update={
        "mcp_auth_enabled": True, "mcp_auth_provider": "github",
        "mcp_github_client_id": None, "mcp_github_client_secret": None,
    })
    with pytest.raises(ConfigurationError):
        build_auth_provider(s, BASE_URL)


def test_build_auth_provider_github_ok() -> None:
    s = get_settings().model_copy(update={
        "mcp_auth_enabled": True, "mcp_auth_provider": "github",
        "mcp_github_client_id": "cid", "mcp_github_client_secret": "secret",
    })
    provider = build_auth_provider(s, BASE_URL)
    assert type(provider).__name__ == "GitHubProvider"


def test_build_auth_provider_google_ok() -> None:
    s = get_settings().model_copy(update={
        "mcp_auth_enabled": True, "mcp_auth_provider": "google",
        "mcp_google_client_id": "cid", "mcp_google_client_secret": "secret",
    })
    provider = build_auth_provider(s, BASE_URL)
    assert type(provider).__name__ == "GoogleProvider"


# --------------------------------------------------------------------------- #
# tools via in-memory client                                                   #
# --------------------------------------------------------------------------- #
async def test_tools_registered(mcp_client) -> None:
    tools = await mcp_client.list_tools()
    names = {t.name for t in tools}
    assert {"whoami", "dispense_key", "rotate_key", "list_audit"} <= names
    assert len(names) == 19


async def test_whoami(mcp_client) -> None:
    result = await mcp_client.call_tool("whoami", {})
    assert result.data.email == ADMIN_EMAIL
    assert result.data.role == Role.ADMIN.value


async def test_key_lifecycle(mcp_client) -> None:
    reg = await mcp_client.call_tool("register_key", {"name": "K1", "api_key": _nvapi()})
    key_id = reg.data.id
    assert reg.data.status == "active"

    listing = await mcp_client.call_tool("list_keys", {})
    assert any(k.id == key_id for k in listing.data)

    got = await mcp_client.call_tool("get_key", {"key_id": key_id})
    assert got.data.id == key_id

    disp = await mcp_client.call_tool("dispense_key", {})
    assert disp.data.api_key.startswith("nvapi-")

    val = await mcp_client.call_tool("validate_key", {"key_id": key_id})
    assert val.data.result == "valid"

    rot = await mcp_client.call_tool("rotate_key", {"key_id": key_id, "new_api_key": _nvapi()})
    assert rot.data.status == "active" and rot.data.id != key_id

    rev = await mcp_client.call_tool("revoke_key", {"key_id": rot.data.id})
    assert rev.data.status == "revoked"

    deleted = await mcp_client.call_tool("delete_key", {"key_id": key_id})
    assert deleted.data["status"] == "deleted"


async def test_register_key_rejects_bad_prefix(mcp_client) -> None:
    with pytest.raises(ToolError):
        await mcp_client.call_tool("register_key", {"name": "bad", "api_key": "sk-nope"})


async def test_register_key_bad_uuid(mcp_client) -> None:
    with pytest.raises(ToolError):
        await mcp_client.call_tool("get_key", {"key_id": "not-a-uuid"})


async def test_project_lifecycle_and_assignment(mcp_client) -> None:
    proj = await mcp_client.call_tool("create_project", {"name": "P", "description": "d"})
    pid = proj.data.id
    upd = await mcp_client.call_tool("update_project", {"project_id": pid, "name": "P2"})
    assert upd.data.name == "P2"
    got = await mcp_client.call_tool("get_project", {"project_id": pid})
    assert got.data.id == pid
    plist = await mcp_client.call_tool("list_projects", {})
    assert any(p.id == pid for p in plist.data)

    reg = await mcp_client.call_tool("register_key", {"name": "K", "api_key": _nvapi()})
    assigned = await mcp_client.call_tool(
        "assign_key_to_project", {"project_id": pid, "key_id": reg.data.id}
    )
    assert assigned.data.project_id == pid

    scoped = await mcp_client.call_tool("dispense_key", {"project_id": pid})
    assert scoped.data.project_id == pid

    delp = await mcp_client.call_tool("delete_project", {"project_id": pid})
    assert delp.data["status"] == "deleted"


async def test_stats_audit_and_expiry(mcp_client) -> None:
    await mcp_client.call_tool("register_key", {"name": "K", "api_key": _nvapi()})
    overview = await mcp_client.call_tool("stats_overview", {})
    assert overview.data.total_keys == 1
    usage = await mcp_client.call_tool("usage_stats", {"days": 7})
    assert isinstance(usage.data, list)
    expired = await mcp_client.call_tool("check_expirations", {})
    assert isinstance(expired.data, list)
    audit = await mcp_client.call_tool("list_audit", {"limit": 10})
    assert any(e.action == "key.registered" for e in audit.data)


async def test_rbac_viewer_cannot_dispense(monkeypatch) -> None:
    await _reset_db()
    await _seed_admin()
    await _seed_viewer()
    settings = get_settings()
    monkeypatch.setattr(settings, "mcp_auth_enabled", False)
    monkeypatch.setattr(settings, "mcp_dev_identity", VIEWER_EMAIL)
    monkeypatch.setattr(server_mod, "NvidiaKeyValidator", lambda *a, **k: _FakeValidator())
    server = server_mod.build_mcp_server(settings, BASE_URL)
    async with Client(server) as client:
        await client.call_tool("list_keys", {})  # viewer allowed
        with pytest.raises(ToolError) as excinfo:
            await client.call_tool("dispense_key", {})
        assert "permission" in str(excinfo.value).lower()


# --------------------------------------------------------------------------- #
# ASGI composition                                                             #
# --------------------------------------------------------------------------- #
def test_mount_disabled_returns_same_app() -> None:
    settings = get_settings().model_copy(update={"mcp_enabled": False})
    sentinel = object()
    assert mount_mcp_connector(sentinel, settings) is sentinel  # type: ignore[arg-type]


def test_mount_requires_base_url_when_authenticated(monkeypatch) -> None:
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    settings = get_settings().model_copy(update={
        "mcp_enabled": True, "mcp_auth_enabled": True, "public_base_url": None,
    })
    with pytest.raises(ConfigurationError):
        mount_mcp_connector(object(), settings)  # type: ignore[arg-type]


def test_composed_app_serves_api_and_mounts_mcp(monkeypatch) -> None:
    from starlette.testclient import TestClient

    from app.main import create_asgi_app

    settings = get_settings()
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_auth_enabled", False)
    monkeypatch.setattr(settings, "public_base_url", "http://testserver")
    app = create_asgi_app()
    with TestClient(app) as tc:
        assert tc.get("/health").status_code == 200
        assert tc.get("/").status_code == 200
        assert tc.get("/mcp").status_code != 404

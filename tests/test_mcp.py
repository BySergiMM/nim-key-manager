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
from app.mcp.identity import (
    Identity,
    extract_identity,
    is_allowed,
    parse_allow_list,
    resolve_actor,
    resolve_email,
)
from tests.conftest import RecordingLogger

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
        await AuthService(session).bootstrap_admin(
            email=ADMIN_EMAIL, password="SuperSecret123", full_name="Admin"
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


def test_resolve_email_ignores_an_address_the_provider_does_not_vouch_for() -> None:
    unverified = Identity("sub-9", None, "admin@ex.com", None, email_verified=False)
    assert resolve_email(unverified) == "sub-9@mcp.local"
    assert resolve_email(Identity("sub-9", None, "admin@ex.com", None, email_verified=True)) == (
        "admin@ex.com"
    )


def _settings(allowed: list[str], provider: str = "github"):
    return get_settings().model_copy(
        update={"mcp_allowed_identities": allowed, "mcp_auth_provider": provider}
    )


def test_is_allowed_matrix() -> None:
    s = _settings(["98814441", "me@ex.com"])
    assert is_allowed(Identity("98814441", "octocat", None, None), s) is True  # by account id
    assert is_allowed(Identity("1", None, "me@ex.com", None), s) is True  # by e-mail
    assert is_allowed(Identity("1", None, "ME@Ex.com", None), s) is True  # case-insensitive
    assert is_allowed(Identity("2", "stranger", "x@y.z", None), s) is False
    assert is_allowed(Identity("98814442", "octocat", None, None), s) is False  # other id
    assert is_allowed(Identity("1", "octocat", None, None), _settings([])) is False  # fail-closed


def test_a_github_login_never_matches() -> None:
    """Logins are renamed and re-registered: the entry that used to work must not match."""
    s = _settings(["octocat"])
    assert is_allowed(Identity("98814441", "octocat", None, None), s) is False
    # The old code also matched the e-mail it derived from the login.
    s = _settings(["octocat@users.noreply.github.com"])
    assert is_allowed(Identity("5", "octocat", None, None), s) is False


def test_a_login_made_of_digits_does_not_pass_for_an_account_id() -> None:
    """GitHub logins may be all digits: a numeric entry matches the account id only."""
    s = _settings(["12345"])
    assert is_allowed(Identity("999", "12345", None, None), s) is False
    assert is_allowed(Identity("12345", "somebody", None, None), s) is True


def test_google_emails_match_only_when_google_vouches_for_them() -> None:
    s = _settings(["me@ex.com"], provider="google")
    assert is_allowed(Identity("1", None, "me@ex.com", None, email_verified=True), s) is True
    assert is_allowed(Identity("1", None, "me@ex.com", None, email_verified=False), s) is False
    assert is_allowed(Identity("1", None, "me@ex.com", None), s) is False  # silence is not proof
    # The numeric Google subject needs no such proof.
    assert is_allowed(Identity("112233", None, None, None), _settings(["112233"], "google")) is True


def test_a_github_email_is_refused_when_the_claim_says_it_is_unverified() -> None:
    s = _settings(["me@ex.com"])
    assert is_allowed(Identity("1", None, "me@ex.com", None, email_verified=False), s) is False


def test_parse_allow_list_sorts_entries_by_what_they_can_match() -> None:
    allow = parse_allow_list(
        [" 123 ", "Me@Ex.com", "octocat", "@octocat", "two words", "", "   ", "١٢٣"]
    )
    assert allow.ids == {"123"}
    assert allow.emails == {"me@ex.com"}
    assert allow.ignored == ("octocat", "@octocat", "two words", "١٢٣")


@pytest.mark.parametrize(
    ("claim", "expected"),
    [(True, True), ("true", True), ("True", True), (False, False), ("false", False), (None, None)],
)
def test_extract_identity_reads_the_provider_claims(monkeypatch, claim, expected) -> None:
    from fastmcp.server.auth.auth import AccessToken

    token = AccessToken(
        token="t", client_id="98814441", scopes=[],
        claims={"sub": "98814441", "login": "octocat", "email": "o@ex.com",
                "name": "Octo", "email_verified": claim},
    )
    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: token)
    identity = extract_identity()
    assert identity is not None
    assert (identity.subject, identity.login, identity.email) == ("98814441", "octocat", "o@ex.com")
    assert identity.email_verified is expected


# --------------------------------------------------------------------------- #
# resolve_actor (auth enabled, identity injected)                             #
# --------------------------------------------------------------------------- #
async def test_resolve_actor_auto_provisions_allowed_identity(monkeypatch) -> None:
    await _reset_db()
    await _seed_admin()
    monkeypatch.setattr(identity_mod, "extract_identity",
                        lambda: Identity("42", "octocat", "octo@ex.com", "Octo"))
    settings = get_settings().model_copy(update={
        "mcp_auth_enabled": True, "mcp_allowed_identities": ["42"],
        "mcp_auto_provision": True, "mcp_default_role": Role.MANAGER,
    })
    async with SessionFactory() as session:
        actor = await resolve_actor(session, settings)
        assert actor.email == "octo@ex.com"
        assert actor.role == Role.MANAGER.value  # an explicit MCP_DEFAULT_ROLE is honored


def test_the_default_role_of_a_provisioned_user_is_viewer() -> None:
    from app.core.config import Settings

    assert Settings.model_fields["mcp_default_role"].default == Role.VIEWER


async def test_resolve_actor_provisions_a_viewer_unless_told_otherwise(monkeypatch) -> None:
    """Auto-provisioned connector users used to be administrators."""
    await _reset_db()
    await _seed_admin()
    monkeypatch.setattr(identity_mod, "extract_identity",
                        lambda: Identity("42", "octocat", "octo@ex.com", "Octo"))
    from app.core.config import Settings

    # MCP_DEFAULT_ROLE is not exported, so this is the class default.
    settings = Settings(
        _env_file=None, mcp_auth_enabled=True, mcp_allowed_identities=["42"],
        mcp_auto_provision=True,
    )
    async with SessionFactory() as session:
        actor = await resolve_actor(session, settings)
        assert actor.role == Role.VIEWER.value


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


async def test_an_unverified_address_does_not_lend_the_account_an_existing_users_role(
    monkeypatch,
) -> None:
    """Allowed by account id, but claiming the administrator's address without Google's word."""
    await _reset_db()
    await _seed_admin()
    monkeypatch.setattr(
        identity_mod, "extract_identity",
        lambda: Identity("77", None, ADMIN_EMAIL, "Mallory", email_verified=False),
    )
    settings = get_settings().model_copy(update={
        "mcp_auth_enabled": True, "mcp_auth_provider": "google",
        "mcp_allowed_identities": ["77"], "mcp_auto_provision": True,
    })
    async with SessionFactory() as session:
        actor = await resolve_actor(session, settings)
        assert actor.email == "77@mcp.local"  # not the administrator's row
        assert actor.role == Role.VIEWER.value


async def test_resolve_actor_rejects_unlisted_identity(monkeypatch) -> None:
    await _reset_db()
    await _seed_admin()
    monkeypatch.setattr(identity_mod, "extract_identity",
                        lambda: Identity("9", "stranger", "s@ex.com", None))
    settings = get_settings().model_copy(update={
        "mcp_auth_enabled": True, "mcp_allowed_identities": ["42"],
    })
    async with SessionFactory() as session:
        with pytest.raises(PermissionDeniedError):
            await resolve_actor(session, settings)


async def test_a_denied_identity_is_logged_with_its_account_id_but_not_told_it(monkeypatch) -> None:
    """The operator needs the id to fill MCP_ALLOWED_IDENTITIES; the caller learns nothing."""
    await _reset_db()
    await _seed_admin()
    recorder = RecordingLogger()
    monkeypatch.setattr(identity_mod, "logger", recorder)
    monkeypatch.setattr(identity_mod, "extract_identity",
                        lambda: Identity("98814441", "octocat", "o@ex.com", None))
    settings = get_settings().model_copy(update={
        "mcp_auth_enabled": True, "mcp_allowed_identities": ["octocat"],  # a login: ignored
    })
    async with SessionFactory() as session:
        with pytest.raises(PermissionDeniedError) as excinfo:
            await resolve_actor(session, settings)
    assert "98814441" not in str(excinfo.value)
    [(level, event, fields)] = recorder.events
    assert (level, event) == ("warning", "mcp_identity_denied")
    assert fields["subject"] == "98814441" and fields["login"] == "octocat"


async def test_resolve_actor_no_autoprovision_denies(monkeypatch) -> None:
    await _reset_db()
    await _seed_admin()
    monkeypatch.setattr(identity_mod, "extract_identity",
                        lambda: Identity("7", "octocat", "octo@ex.com", None))
    settings = get_settings().model_copy(update={
        "mcp_auth_enabled": True, "mcp_allowed_identities": ["7"],
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
    """A connector that has credentials but no public URL is half configured: fatal."""
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    settings = get_settings().model_copy(update={
        "mcp_enabled": True, "mcp_auth_enabled": True, "public_base_url": None,
        "mcp_auth_provider": "github",
        "mcp_github_client_id": "cid", "mcp_github_client_secret": "secret",
    })
    with pytest.raises(ConfigurationError):
        mount_mcp_connector(object(), settings)  # type: ignore[arg-type]


_NO_CREDENTIALS = {
    "mcp_enabled": True, "mcp_auth_enabled": True, "public_base_url": BASE_URL,
    "mcp_github_client_id": None, "mcp_github_client_secret": None,
    "mcp_google_client_id": None, "mcp_google_client_secret": None,
}


@pytest.mark.parametrize(
    "overrides",
    [
        {"mcp_auth_provider": "github"},
        {"mcp_auth_provider": "google"},
        # one half of the pair is not enough
        {"mcp_auth_provider": "github", "mcp_github_client_id": "cid"},
        {"mcp_auth_provider": "google", "mcp_google_client_secret": "secret"},
        # credentials of the OTHER provider do not count
        {
            "mcp_auth_provider": "google",
            "mcp_github_client_id": "a",
            "mcp_github_client_secret": "b",
        },
    ],
)
def test_mount_leaves_mcp_off_without_oauth_credentials(monkeypatch, overrides) -> None:
    """Missing OAuth credentials used to abort start-up; now /mcp stays off, with a warning."""
    import app.mcp.asgi as asgi_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(asgi_mod, "logger", recorder)
    settings = get_settings().model_copy(update={**_NO_CREDENTIALS, **overrides})
    sentinel = object()
    assert mount_mcp_connector(sentinel, settings) is sentinel  # type: ignore[arg-type]
    levels = [(level, event) for level, event, _ in recorder.events]
    assert ("warning", "mcp_connector_not_configured") in levels
    provider = overrides["mcp_auth_provider"].upper()
    detail = next(f["detail"] for _, event, f in recorder.events if event.endswith("configured"))
    assert f"MCP_{provider}_CLIENT_ID" in detail and "/mcp is OFF" in detail


def test_mount_mounts_mcp_once_the_credentials_are_set() -> None:
    from app.main import create_app

    settings = get_settings().model_copy(update={
        **_NO_CREDENTIALS, "mcp_auth_provider": "github",
        "mcp_github_client_id": "cid", "mcp_github_client_secret": "secret",
    })
    api = create_app()
    composed = mount_mcp_connector(api, settings)
    assert composed is not api
    assert any(getattr(r, "path", None) == "/mcp" for r in composed.routes)


async def test_app_serves_rest_and_dashboard_when_mcp_has_no_credentials(monkeypatch) -> None:
    """The deploy-button case: Render leaves the sync:false OAuth variables blank."""
    from httpx import ASGITransport, AsyncClient

    from app.main import create_asgi_app

    await _reset_db()
    settings = get_settings()
    for field, value in _NO_CREDENTIALS.items():
        monkeypatch.setattr(settings, field, value)
    monkeypatch.setattr(settings, "mcp_auth_provider", "github")
    transport = ASGITransport(app=create_asgi_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        assert (await http.get("/health")).status_code == 200
        assert (await http.get("/")).status_code == 200
        assert (await http.get("/api/v1/auth/me")).status_code == 401  # reached the API
        assert (await http.get("/mcp")).status_code == 404  # connector is off


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


def _mountable(**overrides):
    return get_settings().model_copy(update={
        **_NO_CREDENTIALS, "mcp_auth_provider": "github",
        "mcp_github_client_id": "cid", "mcp_github_client_secret": "secret", **overrides,
    })


def test_mount_warns_about_allow_list_entries_it_will_ignore(monkeypatch) -> None:
    import app.mcp.asgi as asgi_mod
    from app.main import create_app

    recorder = RecordingLogger()
    monkeypatch.setattr(asgi_mod, "logger", recorder)
    mount_mcp_connector(create_app(), _mountable(mcp_allowed_identities=["octocat", "42"]))
    [(level, event, fields)] = [e for e in recorder.events if "allow_list" in e[1]]
    assert (level, event) == ("warning", "mcp_allow_list_entries_ignored")
    assert fields["entries"] == ["octocat"]  # the id is fine, the login is not
    assert "numeric" in fields["detail"]


def test_mount_warns_when_nobody_can_connect(monkeypatch) -> None:
    import app.mcp.asgi as asgi_mod
    from app.main import create_app

    recorder = RecordingLogger()
    monkeypatch.setattr(asgi_mod, "logger", recorder)
    mount_mcp_connector(create_app(), _mountable(mcp_allowed_identities=["octocat"]))
    events = {event for _, event, _ in recorder.events}
    assert {"mcp_allow_list_entries_ignored", "mcp_allow_list_empty"} <= events


def test_mount_is_quiet_about_a_good_allow_list(monkeypatch) -> None:
    import app.mcp.asgi as asgi_mod
    from app.main import create_app

    recorder = RecordingLogger()
    monkeypatch.setattr(asgi_mod, "logger", recorder)
    mount_mcp_connector(create_app(), _mountable(mcp_allowed_identities=["42", "me@ex.com"]))
    assert not [e for e in recorder.events if "allow_list" in e[1]]

"""Tests for the ``nimkm`` command line interface and install paths."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import cli
from app.core import config as config_mod
from app.core import paths

REPO_ROOT = Path(__file__).resolve().parents[1]
# Environment keys the test session injects; a CLI subprocess must not see them.
_TEST_ONLY_ENV = (
    "DATABASE_URL", "JWT_SECRET", "ENCRYPTION_MASTER_KEY", "AUTO_CREATE_TABLES",
    "ENVIRONMENT", "DEBUG", "SCHEDULER_ENABLED", "RATE_LIMIT_ENABLED",
    "METRICS_ENABLED", "FIRST_ADMIN_EMAIL", "FIRST_ADMIN_PASSWORD",
)


# --------------------------------------------------------------------------- #
# paths                                                                        #
# --------------------------------------------------------------------------- #
def test_home_honours_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    assert paths.home() == tmp_path
    assert paths.config_file() == tmp_path / "config.env"
    assert paths.data_dir() == tmp_path / "data"


def test_default_database_url_is_absolute(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    url = paths.default_database_url()
    assert url.startswith("sqlite+aiosqlite:///")
    assert paths.sqlite_path(url) == tmp_path / "data" / "nimkm.db"


def test_sqlite_path_ignores_other_engines() -> None:
    assert paths.sqlite_path("postgresql+asyncpg://u:p@h/db") is None
    assert paths.sqlite_path("sqlite+aiosqlite:///:memory:") is None


def test_ensure_dirs_creates_layout(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path / "nested"))
    root = paths.ensure_dirs()
    assert root.is_dir() and paths.data_dir().is_dir() and paths.logs_dir().is_dir()


# --------------------------------------------------------------------------- #
# settings guardrails                                                          #
# --------------------------------------------------------------------------- #
def test_insecure_defaults_detected() -> None:
    settings = config_mod.Settings(
        jwt_secret=config_mod.INSECURE_JWT_SECRET,
        encryption_master_key=config_mod.INSECURE_MASTER_KEY,
        environment="production",
    )
    assert settings.insecure_defaults() == ["JWT_SECRET", "ENCRYPTION_MASTER_KEY"]
    with pytest.raises(Exception, match="placeholder"):
        settings.require_secure_secrets()


def test_insecure_defaults_allowed_in_development() -> None:
    settings = config_mod.Settings(
        jwt_secret=config_mod.INSECURE_JWT_SECRET, environment="development"
    )
    settings.require_secure_secrets()  # must not raise
    assert settings.is_local_environment


def test_real_secrets_pass_the_gate() -> None:
    settings = config_mod.Settings(
        jwt_secret="a" * 40, encryption_master_key="b" * 40, environment="production"
    )
    assert settings.insecure_defaults() == []
    settings.require_secure_secrets()


def test_mcp_credentials_configured_per_provider() -> None:
    github = config_mod.Settings(mcp_github_client_id="id", mcp_github_client_secret="s")
    assert github.mcp_credentials_configured() is True
    google = config_mod.Settings(mcp_auth_provider="google", mcp_github_client_id="id")
    assert google.mcp_credentials_configured() is False


# --------------------------------------------------------------------------- #
# configuration file handling                                                  #
# --------------------------------------------------------------------------- #
def test_config_roundtrip(tmp_path) -> None:
    target = tmp_path / "config.env"
    cli.write_config({"A": "1", "QUOTED": '"x y"'}, target)
    values = cli.read_config(target)
    assert values == {"A": "1", "QUOTED": "x y"}
    assert target.read_text(encoding="utf-8").startswith("# NIM Key Manager")


def test_read_config_missing_file(tmp_path) -> None:
    assert cli.read_config(tmp_path / "nope.env") == {}


def test_config_file_is_owner_only(tmp_path) -> None:
    target = tmp_path / "config.env"
    cli.write_config({"JWT_SECRET": "x"}, target)
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600


def test_default_config_generates_distinct_secrets() -> None:
    values = cli._default_config("sqlite+aiosqlite:///x.db", "127.0.0.1", 8000)
    secrets_used = {values["JWT_SECRET"], values["ENCRYPTION_MASTER_KEY"]}
    assert len(secrets_used) == 2
    assert all(len(value) >= 40 for value in secrets_used)
    assert values["PUBLIC_BASE_URL"] == "http://127.0.0.1:8000"
    assert values["AUTO_CREATE_TABLES"] == "false"


def test_default_config_maps_wildcard_host_to_loopback() -> None:
    values = cli._default_config("sqlite+aiosqlite:///x.db", "0.0.0.0", 9000)
    assert values["PUBLIC_BASE_URL"] == "http://127.0.0.1:9000"


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def test_browsable_url_normalises_wildcards() -> None:
    assert cli._browsable_url("0.0.0.0", 8000) == "http://127.0.0.1:8000"
    assert cli._browsable_url("localhost", 80) == "http://localhost:80"


def test_port_is_free_detects_a_listening_socket() -> None:
    import socket

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(5)
        busy = server.getsockname()[1]
        assert cli._port_is_free("127.0.0.1", busy) is False


def test_first_free_port_skips_busy_ones(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_port_is_free", lambda host, port: port not in (8000, 8001))
    assert cli._first_free_port("127.0.0.1", 8000) == 8002


def test_first_free_port_falls_back_when_all_are_busy(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_port_is_free", lambda host, port: False)
    assert cli._first_free_port("127.0.0.1", 9000) == 9000


def test_oauth_console_urls() -> None:
    assert "github.com" in cli._oauth_console("github")
    assert "google" in cli._oauth_console("google")
    assert cli._oauth_console("other") == "the provider console"


# --------------------------------------------------------------------------- #
# argument parsing                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "argv",
    [
        ["mcp"], ["mcp", "setup"], ["mcp", "serve"], ["mcp", "status"],
        ["mcp", "remove"], ["mcp", "oauth"], ["setup"],
        ["up"], ["init"], ["web"], ["serve"], ["migrate"], ["doctor"], ["status"],
        ["config", "path"], ["admin", "list"], ["update"], ["uninstall"], ["version"],
    ],
)
def test_parser_wires_every_command(argv) -> None:
    args = cli.build_parser().parse_args(argv)
    assert callable(args.func)


def test_web_and_serve_are_the_same_command() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["web"]).func is parser.parse_args(["serve"]).func


def test_setup_alias_matches_mcp_setup() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["setup"]).func is parser.parse_args(["mcp", "setup"]).func


def test_bare_invocation_prints_help(capsys) -> None:
    assert cli.main([]) == 0
    assert "nimkm" in capsys.readouterr().out


def test_version_command(capsys) -> None:
    assert cli.main(["version", "-v"]) == 0
    output = capsys.readouterr().out
    assert cli.__version__ in output
    assert "runtime" in output and "mcp serve" in output
    # The implementation must stay invisible.
    assert "python" not in output.lower() and "uvicorn" not in output.lower()


def test_admin_requires_email() -> None:
    with pytest.raises(SystemExit):
        cli.main(["admin", "create"])


def test_startup_blocked_without_config_or_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path / "empty"))
    unconfigured = config_mod.Settings(
        jwt_secret=config_mod.INSECURE_JWT_SECRET,
        encryption_master_key=config_mod.INSECURE_MASTER_KEY,
    )
    assert "nimkm init" in (cli._startup_blocker(unconfigured) or "")


def test_startup_allowed_with_env_only_secrets(tmp_path, monkeypatch) -> None:
    """Containers configure everything through the environment: no config.env."""
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path / "empty"))
    from_env = config_mod.Settings(jwt_secret="x" * 40, encryption_master_key="y" * 40)
    assert cli._startup_blocker(from_env) is None


def test_config_show_masks_secrets(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    cli.write_config({"JWT_SECRET": "supersecretvalue", "HOST": "127.0.0.1"})
    assert cli.main(["config", "show"]) == 0
    output = capsys.readouterr().out
    assert "supersecretvalue" not in output
    assert "HOST=127.0.0.1" in output


def test_config_set_updates_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    cli.write_config({"HOST": "127.0.0.1"})
    assert cli.main(["config", "set", "port=9001"]) == 0
    assert cli.read_config()["PORT"] == "9001"


def test_config_set_rejects_bad_assignment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    cli.write_config({"HOST": "127.0.0.1"})
    assert cli.main(["config", "set", "oops"]) == 1


def test_config_path_without_install(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path / "fresh"))
    assert cli.main(["config", "path"]) == 0
    assert "config.env" in capsys.readouterr().out


def test_unexpected_errors_are_reported_not_raised(monkeypatch, capsys) -> None:
    def explode(args: object) -> int:
        raise RuntimeError("boom")

    monkeypatch.delenv("NIMKM_DEBUG", raising=False)
    monkeypatch.setattr(cli, "cmd_version", explode)
    assert cli.main(["version"]) == 1
    assert "RuntimeError: boom" in capsys.readouterr().err


def test_keyboard_interrupt_exits_quietly(monkeypatch) -> None:
    def interrupt(args: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cmd_version", interrupt)
    assert cli.main(["version"]) == 130


def test_uninstall_refuses_non_interactive_without_yes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    assert cli.main(["uninstall"]) == 1


def test_uninstall_removes_runtime_but_keeps_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    paths.ensure_dirs()
    paths.runtime_dir().mkdir()
    paths.bin_dir().mkdir()
    cli.write_config({"HOST": "127.0.0.1"})
    assert cli.main(["uninstall", "--yes"]) == 0
    assert not paths.runtime_dir().exists()
    assert paths.config_file().exists()


def test_uninstall_purge_removes_everything(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path / "home"))
    paths.ensure_dirs()
    cli.write_config({"HOST": "127.0.0.1"})
    assert cli.main(["uninstall", "--purge", "--yes"]) == 0
    assert not paths.home().exists()


def test_status_signals_not_connected(tmp_path, monkeypatch, capsys) -> None:
    """Exit 2 when no client is registered, so scripts can act on it."""
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(cli, "_http_json", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert cli.main(["status"]) == cli.NOTHING_CHANGED
    assert "not running" in capsys.readouterr().out


def test_doctor_fails_without_configuration(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path / "absent"))
    assert cli.main(["doctor", "--offline"]) == 1
    assert "nimkm init" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# commands that touch the database                                             #
# --------------------------------------------------------------------------- #
@pytest.fixture
def empty_database():
    """A clean schema, so user counts are deterministic across test files."""
    from app.infrastructure.db.models import Base
    from app.infrastructure.db.session import engine

    async def _reset() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    cli._run_async(_reset())
    yield


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated install home; migrations are exercised end-to-end elsewhere."""
    from app.infrastructure.db import migrations

    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path / "home"))
    monkeypatch.setattr(migrations, "upgrade", lambda revision="head": None)
    return tmp_path / "home"


def test_init_creates_config_database_and_admin(home, empty_database, capsys) -> None:
    assert cli.main(["init", "--no-input", "--port", "8321"]) == 0
    output = capsys.readouterr().out
    values = cli.read_config(home / "config.env")

    assert values["PORT"] == "8321"
    assert values["ENVIRONMENT"] == "production"
    assert len(values["JWT_SECRET"]) >= 40
    assert cli.DEFAULT_ADMIN_EMAIL in output
    assert "password" in output and "shown once" in output


def test_the_default_admin_address_can_be_serialised() -> None:
    """It bypasses the request schema at creation but every response validates it.

    `admin@localhost` shipped once and broke every MCP tool call after a normal
    install, because the account is created through the CLI, not the API.
    """
    from pydantic import BaseModel, EmailStr

    class Probe(BaseModel):
        email: EmailStr

    assert Probe(email=cli.DEFAULT_ADMIN_EMAIL).email == cli.DEFAULT_ADMIN_EMAIL


@pytest.mark.parametrize("address", ["admin@localhost", "not-an-email", "a@b", "x@y.local"])
def test_unusable_admin_addresses_are_refused_up_front(address) -> None:
    with pytest.raises(SystemExit, match="cannot be used"):
        cli._require_valid_email(address)


def test_init_refuses_an_unusable_admin_address(home, empty_database) -> None:
    with pytest.raises(SystemExit):
        cli.main(["init", "--no-input", "--admin-email", "admin@localhost"])


def test_admin_create_refuses_an_unusable_address(home, empty_database) -> None:
    with pytest.raises(SystemExit):
        cli.main(["admin", "create", "--email", "nope@localhost"])


def test_init_accepts_explicit_admin_credentials(home, empty_database, capsys) -> None:
    assert cli.main([
        "init", "--no-input",
        "--admin-email", "owner@example.com", "--admin-password", "Sup3rSecret!",
    ]) == 0
    assert "owner@example.com" in capsys.readouterr().out
    assert cli.main(["admin", "list"]) == 0
    assert "owner@example.com" in capsys.readouterr().out


def test_init_is_idempotent(home, empty_database, capsys) -> None:
    assert cli.main(["init", "--no-input"]) == 0
    first = cli.read_config(home / "config.env")
    capsys.readouterr()
    assert cli.main(["init", "--no-input"]) == 0
    assert cli.read_config(home / "config.env")["JWT_SECRET"] == first["JWT_SECRET"]
    assert "already present" in capsys.readouterr().out


def test_init_force_backs_up_but_keeps_the_encryption_key(home, empty_database) -> None:
    """Rotating ENCRYPTION_MASTER_KEY would orphan every stored API key."""
    assert cli.main(["init", "--no-input"]) == 0
    first = cli.read_config(home / "config.env")
    assert cli.main(["init", "--no-input", "--force"]) == 0
    regenerated = cli.read_config(home / "config.env")
    assert regenerated["JWT_SECRET"] != first["JWT_SECRET"]
    assert regenerated["ENCRYPTION_MASTER_KEY"] == first["ENCRYPTION_MASTER_KEY"]
    assert (home / "config.env.bak").exists()


def test_init_switches_to_postgres_url(home, empty_database) -> None:
    url = "postgresql+asyncpg://u:p@db:5432/nimkeys"
    assert cli.main(["init", "--no-input", "--database-url", url]) == 0
    assert cli.read_config(home / "config.env")["DATABASE_URL"] == url


def test_admin_create_requires_an_existing_administrator(home, empty_database) -> None:
    assert cli.main(["init", "--no-input"]) == 0
    assert cli.main(["admin", "create", "--email", "x@example.com", "--role", "viewer"]) == 1


def test_admin_create_with_actor_and_reset_password(home, empty_database, capsys) -> None:
    assert cli.main(["init", "--no-input", "--admin-email", "boss@example.com"]) == 0
    capsys.readouterr()
    assert cli.main([
        "admin", "create", "--email", "dev@example.com",
        "--role", "manager", "--actor", "boss@example.com",
    ]) == 0
    assert "dev@example.com" in capsys.readouterr().out

    assert cli.main(["admin", "reset-password", "--email", "dev@example.com"]) == 0
    assert "password" in capsys.readouterr().out

    assert cli.main(["admin", "reset-password", "--email", "ghost@example.com"]) == 1


def test_admin_list_without_users(home, empty_database) -> None:
    paths.ensure_dirs()
    assert cli.main(["admin", "list"]) == 1


def test_migrate_command_reports_the_revision(home, empty_database, monkeypatch, capsys) -> None:
    from app.infrastructure.db import migrations

    monkeypatch.setattr(migrations, "head_revision", lambda: "0001")
    assert cli.main(["migrate"]) == 0
    assert "0001" in capsys.readouterr().out


def test_doctor_flags_an_unmigrated_database(home, empty_database, capsys) -> None:
    assert cli.main(["init", "--no-input"]) == 0
    capsys.readouterr()
    # `upgrade` was stubbed out, so alembic never stamped a revision.
    assert cli.main(["doctor", "--offline"]) == 1
    report = capsys.readouterr().out
    assert "not migrated" in report
    assert "secrets" in report


def test_doctor_does_not_fault_a_local_only_install(home, empty_database, capsys) -> None:
    """No remote connector is the normal state, not a warning."""
    assert cli.main(["init", "--no-input"]) == 0
    capsys.readouterr()
    cli.main(["doctor", "--offline"])
    report = capsys.readouterr().out
    assert "remote connector" in report
    assert "only needed for remote clients" in report


def test_up_initialises_then_serves(home, empty_database, monkeypatch) -> None:
    served: dict[str, object] = {}

    def fake_serve(args: object) -> int:
        served["host"] = args.host  # type: ignore[attr-defined]
        served["port"] = args.port  # type: ignore[attr-defined]
        return 0

    monkeypatch.setattr(cli, "cmd_serve", fake_serve)
    monkeypatch.setattr(cli, "_port_is_free", lambda host, port: True)
    assert cli.main(["up", "--no-browser", "--no-input", "--port", "8456"]) == 0
    assert served["port"] == 8456
    assert (home / "config.env").exists()


def test_up_picks_another_port_when_busy(home, empty_database, monkeypatch, capsys) -> None:
    assert cli.main(["init", "--no-input", "--port", "8500"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(cli, "cmd_serve", lambda args: 0)
    monkeypatch.setattr(cli, "_port_is_free", lambda host, port: port != 8500)
    assert cli.main(["up", "--no-browser", "--port", "8500"]) == 0
    assert "busy" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# connector helper, updates and editor                                         #
# --------------------------------------------------------------------------- #
def test_mcp_status_reports_idle_remote_connector(home, capsys) -> None:
    paths.ensure_dirs()
    cli.write_config({"HOST": "127.0.0.1"})
    # Exit 2: nothing is registered anywhere, which scripts may want to act on.
    assert cli.main(["mcp", "status"]) == cli.NOTHING_CHANGED
    output = capsys.readouterr().out
    assert "idle" in output
    assert "mcp serve" in output


def test_mcp_oauth_writes_credentials(home, capsys) -> None:
    paths.ensure_dirs()
    cli.write_config({"HOST": "127.0.0.1"})
    assert cli.main([
        "mcp", "oauth", "--provider", "github",
        "--client-id", "cid", "--client-secret", "shh", "--allow", "octocat",
    ]) == 0
    values = cli.read_config()
    assert values["MCP_GITHUB_CLIENT_ID"] == "cid"
    assert values["MCP_ALLOWED_IDENTITIES"] == "octocat"
    assert "remote connector configured" in capsys.readouterr().out


def test_mcp_oauth_rejects_incomplete_input(home) -> None:
    paths.ensure_dirs()
    cli.write_config({"HOST": "127.0.0.1"})
    assert cli.main([
        "mcp", "oauth", "--client-id", "cid", "--client-secret", "shh", "--allow", "",
    ]) == 1


def test_bare_mcp_prints_help(capsys) -> None:
    assert cli.main(["mcp"]) == 0
    assert "setup" in capsys.readouterr().out


def test_update_when_already_current(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_resolve_latest_spec", lambda: (cli.__version__, "url"))
    assert cli.main(["update"]) == 0
    assert "already on the latest" in capsys.readouterr().out


def test_update_explains_unmanaged_installs(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_resolve_latest_spec", lambda: ("99.0.0", "url"))
    monkeypatch.setattr(cli, "_managed_python", lambda: None)
    assert cli.main(["update"]) == 0
    assert "pip install --upgrade" in capsys.readouterr().out


def test_update_without_network(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_resolve_latest_spec", lambda: None)
    assert cli.main(["update"]) == 1


@pytest.mark.parametrize(
    "url,trusted",
    [
        ("https://github.com/o/r/releases/download/v1/x.whl", True),
        ("https://objects.githubusercontent.com/a/b/x.whl", True),
        ("http://github.com/o/r/x.whl", False),              # not TLS
        ("https://github.com.evil.example/x.whl", False),    # look-alike host
        ("https://evil.example/x.whl", False),
        ("file:///tmp/x.whl", False),
    ],
)
def test_updates_only_come_from_trusted_hosts(url, trusted) -> None:
    """The download URL arrives in an API response; it is untrusted input."""
    assert cli._is_trusted_download(url) is trusted


def test_release_asset_on_an_untrusted_host_is_ignored(monkeypatch) -> None:
    release = {
        "tag_name": "v9.9.9",
        "assets": [{"name": "evil.whl", "browser_download_url": "https://evil.example/e.whl"}],
    }
    monkeypatch.setattr(cli, "_http_json", lambda url, **kw: release)
    version, source = cli._resolve_latest_spec()
    assert version == "9.9.9"
    assert source == f"{cli.PYPI_PACKAGE}==9.9.9", "fell back to PyPI instead of the bad URL"


def test_config_edit_invokes_the_editor(home, monkeypatch) -> None:
    paths.ensure_dirs()
    cli.write_config({"HOST": "127.0.0.1"})
    seen: dict[str, object] = {}

    def fake_call(command: list[str]) -> int:
        seen["cmd"] = command
        return 0

    monkeypatch.setenv("EDITOR", "true")
    monkeypatch.setattr(cli.subprocess, "call", fake_call)
    assert cli.main(["config", "edit"]) == 0
    assert "config.env" in str(seen["cmd"])


def test_status_shows_mcp_and_dashboard_together(home, monkeypatch, capsys) -> None:
    """One 'status' concept: registration first, dashboard second."""
    paths.ensure_dirs()
    cli.write_config({"HOST": "127.0.0.1"})
    monkeypatch.setattr(
        cli, "_http_json",
        lambda *a, **k: {"status": "ok", "database": "up", "version": "1.2.0",
                         "environment": "production"},
    )
    cli.main(["status"])
    output = capsys.readouterr().out
    assert "Clients" in output and "Dashboard" in output
    assert "running" in output


def test_status_reports_a_stopped_dashboard(home, monkeypatch, capsys) -> None:
    paths.ensure_dirs()
    cli.write_config({"HOST": "127.0.0.1"})
    monkeypatch.setattr(cli, "_http_json",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    cli.main(["status"])
    assert "nimkm web" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# end-to-end: a real installation in an isolated home                          #
# --------------------------------------------------------------------------- #
def _clean_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _TEST_ONLY_ENV}
    env["NIMKM_HOME"] = str(home)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["NO_COLOR"] = "1"
    return env


def _nimkm(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        env=_clean_env(home), capture_output=True, text=True, timeout=300,
        cwd=str(REPO_ROOT),
    )


@pytest.mark.slow
def test_end_to_end_install_in_isolated_home(tmp_path) -> None:
    """`nimkm init` must produce a working instance from nothing."""
    home = tmp_path / "install"

    created = _nimkm(home, "init", "--no-input", "--port", "8123")
    assert created.returncode == 0, created.stderr or created.stdout
    assert "ready" in created.stdout.lower()

    config = cli.read_config(home / "config.env")
    assert config["DATABASE_URL"].startswith("sqlite+aiosqlite:///")
    assert len(config["ENCRYPTION_MASTER_KEY"]) >= 40
    assert config["PORT"] == "8123"
    assert (home / "data" / "nimkm.db").exists()

    # An administrator exists and the credentials were printed once.
    listed = _nimkm(home, "admin", "list")
    assert listed.returncode == 0, listed.stderr
    assert cli.DEFAULT_ADMIN_EMAIL in listed.stdout

    # Re-running is idempotent: secrets are preserved.
    again = _nimkm(home, "init", "--no-input")
    assert again.returncode == 0, again.stderr
    assert cli.read_config(home / "config.env")["JWT_SECRET"] == config["JWT_SECRET"]

    healthy = _nimkm(home, "doctor", "--offline")
    assert healthy.returncode == 0, healthy.stdout + healthy.stderr
    assert "FAIL" not in healthy.stdout

    # The composed ASGI app (REST + dashboard + idle MCP connector) must build.
    built = subprocess.run(
        [sys.executable, "-c",
         "from app.main import create_asgi_app; create_asgi_app(); print('ok')"],
        env=_clean_env(home), capture_output=True, text=True, timeout=300,
        cwd=str(tmp_path),
    )
    assert built.returncode == 0, built.stderr
    assert "ok" in built.stdout


@pytest.mark.slow
def test_installed_console_script_is_registered() -> None:
    """`pip install` must expose a `nimkm` entry point."""
    from importlib.metadata import entry_points

    scripts = entry_points(group="console_scripts")
    assert any(script.name == "nimkm" for script in scripts), (
        "nimkm console script missing - reinstall with 'pip install -e .'"
    )


@pytest.mark.slow
def test_migrations_ship_inside_the_package() -> None:
    """The wheel must carry the Alembic scripts, not just the source tree."""
    from app.infrastructure.db import migrations

    assert migrations.MIGRATIONS_DIR.is_dir()
    assert (migrations.MIGRATIONS_DIR / "env.py").exists()
    assert list((migrations.MIGRATIONS_DIR / "versions").glob("*.py"))
    assert migrations.head_revision() is not None


def test_installer_scripts_are_present_and_executable() -> None:
    """Both installers must exist; the POSIX one must be executable text."""
    posix = REPO_ROOT / "install.sh"
    powershell = REPO_ROOT / "install.ps1"
    assert posix.exists() and powershell.exists()
    body = posix.read_text(encoding="utf-8")
    assert body.startswith("#!/bin/sh")
    assert "\r\n" not in body, "install.sh must use LF endings to run under sh"
    assert json.dumps(str(powershell))  # trivially serialisable path, keeps linters quiet

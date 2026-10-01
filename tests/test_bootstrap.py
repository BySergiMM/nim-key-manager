"""Who can become the first administrator of a fresh installation.

``POST /api/v1/auth/register`` used to treat "the user table is empty" as an invitation: the
first anonymous caller was made ADMIN, whatever role it asked for. Deploying without
``FIRST_ADMIN_EMAIL`` / ``FIRST_ADMIN_PASSWORD`` (or merely being slower than a scanner after
the first deploy) handed the whole installation, every stored key included, to a stranger.

The first administrator now comes only from code that runs on the host: the start-up seeding
from those two variables and ``scripts/create_admin.py``. No HTTP request can create one.
"""

from __future__ import annotations

import importlib.util
import secrets
import sys
from pathlib import Path

import pytest

from app.application.services.auth_service import AuthService
from app.core.config import get_settings
from app.domain.enums import Role
from app.domain.exceptions import ConfigurationError, ConflictError, PermissionDeniedError
from app.infrastructure.db.repositories import UserRepository
from app.infrastructure.db.session import SessionFactory
from app.main import _seed_first_admin, create_app
from tests.conftest import ADMIN, RecordingLogger

ROOT = Path(__file__).resolve().parent.parent
ATTACKER = {"email": "attacker@example.com", "password": "Password123!"}
EXAMPLE_PASSWORD = "change-me-strong-password"  # the one .env.example ships


async def count_users() -> int:
    async with SessionFactory() as session:
        return await UserRepository(session).count()


async def login(client, email: str, password: str):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


def configure_first_admin(monkeypatch, email: str | None, password: str | None) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "first_admin_email", email)
    monkeypatch.setattr(settings, "first_admin_password", password)


# --------------------------------------------------------------------------- #
# the attack                                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("role", ["admin", "manager", "viewer"])
async def test_nobody_can_register_on_a_fresh_installation(client, role):
    """No FIRST_ADMIN_*, no users: the first stranger must not get in, whatever role it asks."""
    response = await client.post("/api/v1/auth/register", json={**ATTACKER, "role": role})
    assert response.status_code == 403, response.text
    assert await count_users() == 0
    assert (await login(client, **ATTACKER)).status_code == 401


async def test_the_service_never_creates_a_user_for_nobody(client):
    async with SessionFactory() as session:
        with pytest.raises(PermissionDeniedError):
            await AuthService(session).register(
                email=ATTACKER["email"], password=ATTACKER["password"], full_name=None,
                role=Role.ADMIN, actor=None,
            )
        assert await UserRepository(session).count() == 0


async def test_a_non_administrator_cannot_register_users_either(client, admin_headers):
    manager = {"email": "manager@example.com", "password": "Password123!", "role": "manager"}
    created = await client.post("/api/v1/users", json=manager, headers=admin_headers)
    assert created.status_code == 201
    token = (await login(client, manager["email"], manager["password"])).json()["access_token"]
    response = await client.post(
        "/api/v1/auth/register",
        json={**ATTACKER, "role": "admin"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# the legitimate bootstrap                                                     #
# --------------------------------------------------------------------------- #
async def test_the_first_administrator_comes_from_the_environment(client, monkeypatch):
    configure_first_admin(monkeypatch, ADMIN["email"], ADMIN["password"])
    await _seed_first_admin()

    response = await login(client, ADMIN["email"], ADMIN["password"])
    assert response.status_code == 200
    token = response.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["role"] == "admin"

    # ...and from then on registration is the administrators' business only.
    closed = await client.post("/api/v1/auth/register", json={**ATTACKER, "role": "admin"})
    assert closed.status_code == 403
    assert await count_users() == 1


async def test_seeding_changes_nothing_once_there_are_users(client, admin_headers, monkeypatch):
    configure_first_admin(monkeypatch, "second@example.com", "Password123!")
    recorder = RecordingLogger()
    monkeypatch.setattr("app.main.logger", recorder)
    await _seed_first_admin()
    assert await count_users() == 1
    assert recorder.events == []


async def test_the_first_administrator_can_only_be_created_on_an_empty_installation(
    client, admin_headers
):
    async with SessionFactory() as session:
        with pytest.raises(ConflictError):
            await AuthService(session).bootstrap_admin(
                email="second@example.com", password="Password123!"
            )
        assert await UserRepository(session).get_by_email("second@example.com") is None


def run_as_production(monkeypatch) -> None:
    """Production with good secrets, so only FIRST_ADMIN_PASSWORD can be what is judged."""
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret", secrets.token_urlsafe(48))
    monkeypatch.setattr(settings, "encryption_master_key", secrets.token_urlsafe(48))


async def test_a_leftover_example_password_does_not_stop_an_installation_that_has_users(
    client, admin_headers, monkeypatch
):
    """Restarting a running service must not fail because of a variable nobody reads any more."""
    run_as_production(monkeypatch)
    configure_first_admin(monkeypatch, ADMIN["email"], EXAMPLE_PASSWORD)
    recorder = RecordingLogger()
    monkeypatch.setattr("app.main.logger", recorder)

    app = create_app()  # the start-up secret check used to refuse here
    async with app.router.lifespan_context(app):  # ...and the seeding runs on start-up
        pass

    assert await count_users() == 1
    [(level, event, fields)] = recorder.events
    assert (level, event) == ("warning", "first_admin_password_ignored")
    assert EXAMPLE_PASSWORD not in str(fields)
    assert (await login(client, ADMIN["email"], ADMIN["password"])).status_code == 200


async def test_the_example_password_never_creates_the_first_administrator(client, monkeypatch):
    run_as_production(monkeypatch)
    configure_first_admin(monkeypatch, "boss@example.com", EXAMPLE_PASSWORD)

    with pytest.raises(ConfigurationError) as refused:
        await _seed_first_admin()

    assert await count_users() == 0
    assert "FIRST_ADMIN_PASSWORD" in str(refused.value)
    assert EXAMPLE_PASSWORD not in str(refused.value)
    assert (await login(client, "boss@example.com", EXAMPLE_PASSWORD)).status_code == 401


async def test_a_password_of_your_own_creates_the_first_administrator_in_production(
    client, monkeypatch
):
    run_as_production(monkeypatch)
    configure_first_admin(monkeypatch, "boss@example.com", "correct horse battery staple")
    await _seed_first_admin()
    assert (
        await login(client, "boss@example.com", "correct horse battery staple")
    ).status_code == 200


async def test_the_example_password_still_works_for_local_development(client, monkeypatch):
    """ENVIRONMENT=test here: the check has always been skipped for local environments."""
    configure_first_admin(monkeypatch, "dev@example.com", EXAMPLE_PASSWORD)
    await _seed_first_admin()
    assert (await login(client, "dev@example.com", EXAMPLE_PASSWORD)).status_code == 200


@pytest.mark.parametrize(
    ("email", "password"),
    [(None, None), ("boss@example.com", None), (None, "Password123!"), ("", "")],
)
async def test_a_missing_bootstrap_admin_is_reported_instead_of_leaving_the_door_open(
    client, monkeypatch, email, password
):
    configure_first_admin(monkeypatch, email, password)
    recorder = RecordingLogger()
    monkeypatch.setattr("app.main.logger", recorder)
    await _seed_first_admin()

    assert await count_users() == 0
    [(level, event, fields)] = recorder.events
    assert (level, event) == ("warning", "bootstrap_admin_not_configured")
    assert "FIRST_ADMIN_EMAIL" in fields["detail"]
    assert "Password123!" not in str(fields)


# --------------------------------------------------------------------------- #
# scripts/create_admin.py                                                      #
# --------------------------------------------------------------------------- #
def load_create_admin():
    spec = importlib.util.spec_from_file_location(
        "create_admin_script", ROOT / "scripts" / "create_admin.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run_create_admin(monkeypatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["create_admin.py", *args])
    await load_create_admin().main()


async def test_create_admin_script_creates_the_first_administrator(client, monkeypatch, capsys):
    await run_create_admin(monkeypatch, "root@example.com", "Password123!", "Root")
    assert "Created admin root@example.com" in capsys.readouterr().out
    response = await login(client, "root@example.com", "Password123!")
    assert response.status_code == 200
    async with SessionFactory() as session:
        user = await UserRepository(session).get_by_email("root@example.com")
        assert user is not None and user.role == Role.ADMIN.value


async def test_create_admin_script_promotes_an_existing_user(
    client, admin_headers, monkeypatch, capsys
):
    created = await client.post(
        "/api/v1/users",
        json={"email": "dev@example.com", "password": "Password123!", "role": "viewer"},
        headers=admin_headers,
    )
    assert created.status_code == 201
    await run_create_admin(monkeypatch, "dev@example.com", "ignored")
    assert "Promoted existing user dev@example.com" in capsys.readouterr().out
    async with SessionFactory() as session:
        user = await UserRepository(session).get_by_email("dev@example.com")
        assert user is not None and user.role == Role.ADMIN.value


async def test_create_admin_script_does_not_add_a_stranger_to_a_populated_installation(
    client, admin_headers, monkeypatch
):
    with pytest.raises(ConflictError):
        await run_create_admin(monkeypatch, "stranger@example.com", "Password123!")
    assert await count_users() == 1

"""Startup checks on the configured secrets.

The defaults for ``JWT_SECRET`` and ``ENCRYPTION_MASTER_KEY`` are public placeholders, and
``.env.example`` ships others. Before this check a service started with any of them, so API keys
were encrypted under a key everybody could derive and anybody could sign a valid session token.
"""

from __future__ import annotations

import base64
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import (
    INSECURE_JWT_SECRET,
    INSECURE_MASTER_KEY,
    MIN_SECRET_LENGTH,
    Settings,
    get_settings,
)
from app.domain.exceptions import ConfigurationError
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
STRONG = secrets.token_urlsafe(48)
OTHER_STRONG = secrets.token_urlsafe(48)


def make_settings(**overrides) -> Settings:
    """Settings for the given environment, ignoring whatever the test process exports."""
    values = {
        "environment": "production",
        "jwt_secret": STRONG,
        "encryption_master_key": OTHER_STRONG,
        "first_admin_email": None,
        "first_admin_password": None,
        "mcp_oauth_jwt_signing_key": None,
    }
    return Settings(_env_file=None, **{**values, **overrides})


def refusal(**overrides) -> str:
    with pytest.raises(ConfigurationError) as excinfo:
        make_settings(**overrides).require_secure_secrets()
    return str(excinfo.value)


# --------------------------------------------------------------------------- #
# what is refused                                                              #
# --------------------------------------------------------------------------- #
def test_the_shipped_defaults_are_refused_in_production():
    message = refusal(jwt_secret=INSECURE_JWT_SECRET, encryption_master_key=INSECURE_MASTER_KEY)
    assert "JWT_SECRET" in message and "ENCRYPTION_MASTER_KEY" in message
    assert "ENVIRONMENT=production" in message


def test_an_unconfigured_production_start_is_refused():
    """With nothing exported, every secret is its class default: the public placeholders."""
    unconfigured = Settings.model_construct(environment="production")  # defaults only
    assert unconfigured.jwt_secret == INSECURE_JWT_SECRET
    assert unconfigured.encryption_master_key == INSECURE_MASTER_KEY
    with pytest.raises(ConfigurationError):
        unconfigured.require_secure_secrets()


@pytest.mark.parametrize(
    "placeholder",
    [
        "change-me-to-a-long-random-string",  # .env.example, 33 characters: long enough
        "change-me-to-another-long-random-string",  # .env.example
        "CHANGE-ME-TO-A-LONG-RANDOM-STRING",
        "please-changeme-please-changeme-please",
    ],
)
def test_example_placeholders_are_refused_even_when_long_enough(placeholder):
    assert len(placeholder) >= MIN_SECRET_LENGTH
    assert "placeholder" in refusal(jwt_secret=placeholder)
    assert "placeholder" in refusal(encryption_master_key=placeholder)


@pytest.mark.parametrize("empty", ["", "   ", "\t\n"])
def test_empty_secrets_are_refused(empty):
    assert "JWT_SECRET is empty" in refusal(jwt_secret=empty)
    assert "ENCRYPTION_MASTER_KEY is empty" in refusal(encryption_master_key=empty)


def test_short_secrets_are_refused_at_the_boundary():
    assert "shorter than 32" in refusal(jwt_secret="x" * (MIN_SECRET_LENGTH - 1))
    make_settings(jwt_secret="x" * MIN_SECRET_LENGTH).require_secure_secrets()


def test_the_message_names_the_variables_and_never_prints_a_value():
    weak = "tiny-but-real-secret"
    message = refusal(jwt_secret=weak, encryption_master_key=weak + "-2")
    assert "JWT_SECRET" in message and "ENCRYPTION_MASTER_KEY" in message
    assert weak not in message


def test_optional_secrets_are_checked_only_when_set():
    make_settings().require_secure_secrets()  # unset: derived from JWT_SECRET, fine
    assert "MCP_OAUTH_JWT_SIGNING_KEY" in refusal(mcp_oauth_jwt_signing_key="change-me-" * 4)
    assert "MCP_OAUTH_JWT_SIGNING_KEY" in refusal(mcp_oauth_jwt_signing_key="short")
    make_settings(mcp_oauth_jwt_signing_key=secrets.token_urlsafe(48)).require_secure_secrets()


def test_the_example_admin_password_is_refused_but_any_other_password_is_not_judged():
    assert "FIRST_ADMIN_PASSWORD" in refusal(first_admin_password="change-me-strong-password")
    assert "FIRST_ADMIN_PASSWORD" in refusal(first_admin_password="admin-change-me")
    make_settings(first_admin_password="correct horse battery").require_secure_secrets()


# --------------------------------------------------------------------------- #
# what is accepted                                                             #
# --------------------------------------------------------------------------- #
def test_what_render_generates_is_accepted():
    """``generateValue: true`` yields a randomized, base64-encoded 256-bit value.

    https://render.com/docs/blueprint-spec
    """
    generated = base64.b64encode(secrets.token_bytes(32)).decode()
    assert len(generated) == 44
    make_settings(
        jwt_secret=generated,
        encryption_master_key=base64.b64encode(secrets.token_bytes(32)).decode(),
        mcp_oauth_jwt_signing_key=base64.b64encode(secrets.token_bytes(32)).decode(),
    ).require_secure_secrets()


def test_render_blueprint_generates_every_secret_and_runs_as_production():
    text = (ROOT / "render.yaml").read_text()
    for name in ("JWT_SECRET", "ENCRYPTION_MASTER_KEY", "MCP_OAUTH_JWT_SIGNING_KEY"):
        assert re.search(rf"-\s+key:\s+{name}\s*\n\s+generateValue:\s+true", text), name
    assert re.search(r"-\s+key:\s+ENVIRONMENT\s*\n\s+value:\s+production", text)


@pytest.mark.parametrize("environment", ["development", "Development", "dev", "local", "test"])
def test_local_environments_keep_working_with_the_placeholders(environment):
    settings = Settings(_env_file=None, environment=environment)
    assert settings.is_local_environment
    settings.require_secure_secrets()


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "", "devel"])
def test_every_other_environment_is_treated_as_production(environment):
    assert not Settings(_env_file=None, environment=environment).is_local_environment
    assert refusal(environment=environment, jwt_secret="short")


# --------------------------------------------------------------------------- #
# the application itself                                                       #
# --------------------------------------------------------------------------- #
def test_the_application_will_not_start_in_production_with_the_placeholders(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret", INSECURE_JWT_SECRET)
    monkeypatch.setattr(settings, "encryption_master_key", INSECURE_MASTER_KEY)
    with pytest.raises(ConfigurationError, match="JWT_SECRET"):
        create_app()


def test_the_application_starts_in_production_with_real_secrets(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret", STRONG)
    monkeypatch.setattr(settings, "encryption_master_key", OTHER_STRONG)
    assert create_app().title


def test_a_process_with_no_configuration_at_all_refuses_to_start(tmp_path):
    """The real thing: a fresh container has no environment, so it is "production" with defaults."""
    result = subprocess.run(
        [sys.executable, "-c", "from app.main import create_app; create_app()"],
        cwd=tmp_path,  # no .env here
        env={"PATH": os.environ["PATH"], "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "ConfigurationError" in result.stderr
    assert "JWT_SECRET" in result.stderr and "ENCRYPTION_MASTER_KEY" in result.stderr

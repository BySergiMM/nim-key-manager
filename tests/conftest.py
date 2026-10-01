"""Shared fixtures. Environment is configured BEFORE importing the app package."""

from __future__ import annotations

import os
import tempfile
import uuid

_TMP_DIR = tempfile.mkdtemp(prefix="nimkm-tests-")
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DEBUG": "false",
        "DATABASE_URL": f"sqlite+aiosqlite:///{_TMP_DIR}/test.db",
        "JWT_SECRET": "test-jwt-secret-with-at-least-32-bytes-of-entropy",
        "ENCRYPTION_MASTER_KEY": "test-master-key",
        "AUTO_CREATE_TABLES": "true",
        "SCHEDULER_ENABLED": "false",
        "RATE_LIMIT_ENABLED": "false",
        # The instrumentation middleware interferes with coverage tracing.
        # /metrics stays exposed and tested; per-request metrics are prod-only.
        "METRICS_ENABLED": "false",
        "FIRST_ADMIN_EMAIL": "",
        "FIRST_ADMIN_PASSWORD": "",
    }
)

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.api.deps import get_key_validator  # noqa: E402
from app.api.rate_limit import limiter  # noqa: E402
from app.application.interfaces import KeyValidationOutcome  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.domain.enums import KeyCheckResult  # noqa: E402
from app.infrastructure.db.models import Base  # noqa: E402
from app.infrastructure.db.session import engine  # noqa: E402
from app.main import _seed_first_admin, create_app  # noqa: E402

ADMIN = {"email": "admin@example.com", "password": "SuperSecret123", "full_name": "Admin"}


class FakeValidator:
    """Configurable in-memory stand-in for the NVIDIA gateway."""

    def __init__(self) -> None:
        self.result = KeyCheckResult.VALID
        self.calls = 0

    async def validate(self, api_key: str) -> KeyValidationOutcome:
        self.calls += 1
        status_code = {KeyCheckResult.VALID: 200, KeyCheckResult.INVALID: 401}.get(self.result)
        return KeyValidationOutcome(result=self.result, status_code=status_code)


class RecordingLogger:
    """Stands in for a module's structlog ``logger`` (``cache_logger_on_first_use`` makes
    ``structlog.testing.capture_logs`` unreliable) and keeps ``(level, event, fields)``."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def __getattr__(self, level: str):
        return lambda event, **fields: self.events.append((level, event, fields))


def sample_key(prefix: str = "nvapi-test-") -> str:
    return f"{prefix}{uuid.uuid4().hex}"


@pytest.fixture
def fake_validator() -> FakeValidator:
    return FakeValidator()


@pytest.fixture
async def client(fake_validator: FakeValidator):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    app = create_app()
    app.dependency_overrides[get_key_validator] = lambda: fake_validator
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def rate_limiting(client: AsyncClient):
    """Turn the rate limiter on (the test environment disables it) with clean counters."""
    previous = limiter.enabled
    limiter.enabled = True
    limiter.reset()
    yield
    limiter.enabled = previous
    limiter.reset()


@pytest.fixture
async def admin_headers(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    # The first administrator exists the way it does in production: seeded from
    # FIRST_ADMIN_EMAIL / FIRST_ADMIN_PASSWORD at start-up. No HTTP request can create it.
    settings = get_settings()
    monkeypatch.setattr(settings, "first_admin_email", ADMIN["email"])
    monkeypatch.setattr(settings, "first_admin_password", ADMIN["password"])
    await _seed_first_admin()
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": ADMIN["email"], "password": ADMIN["password"]},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create_user_headers(
    client: AsyncClient,
    admin_headers: dict[str, str],
    email: str,
    role: str,
    password: str = "Password123!",
) -> dict[str, str]:
    response = await client.post(
        "/api/v1/users",
        json={"email": email, "password": password, "role": role},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}

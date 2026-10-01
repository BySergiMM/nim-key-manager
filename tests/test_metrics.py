"""``/metrics`` is not public: an administrator's token or ``METRICS_TOKEN`` opens it.

The endpoint used to answer anybody. Prometheus metrics list every route and carry latencies
and status codes per handler: reconnaissance material for whoever finds the service.
"""

from __future__ import annotations

import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app, create_asgi_app
from tests.conftest import create_user_headers

SCRAPER_TOKEN = secrets.token_urlsafe(32)
PROMETHEUS_MARKER = "# HELP"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def metrics_token(monkeypatch) -> str:
    monkeypatch.setattr(get_settings(), "metrics_token", SCRAPER_TOKEN)
    return SCRAPER_TOKEN


async def test_anonymous_requests_get_no_metrics(client):
    response = await client.get("/metrics")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert PROMETHEUS_MARKER not in response.text


@pytest.mark.parametrize(
    "token",
    ["wrong", "x" * 200, SCRAPER_TOKEN[:-1], SCRAPER_TOKEN + "x"],
    ids=["wrong", "long", "truncated", "extended"],
)
async def test_a_wrong_token_gets_no_metrics(client, metrics_token, token):
    response = await client.get("/metrics", headers=bearer(token))
    assert response.status_code == 401
    assert PROMETHEUS_MARKER not in response.text


async def test_without_a_configured_token_no_token_is_accepted(client):
    """An unset METRICS_TOKEN must not behave like an empty one that anything matches."""
    assert get_settings().metrics_token is None
    for header in ({"Authorization": "Bearer x"}, {"Authorization": "Bearer "}, {}):
        response = await client.get("/metrics", headers=header)
        assert response.status_code == 401, header


async def test_the_metrics_token_opens_it(client, metrics_token):
    response = await client.get("/metrics", headers=bearer(metrics_token))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert PROMETHEUS_MARKER in response.text


async def test_a_non_ascii_credential_is_a_401_not_a_500(client, metrics_token):
    response = await client.get(
        "/metrics", headers=[(b"authorization", "Bearer café".encode("latin-1"))]
    )
    assert response.status_code == 401


async def test_an_administrator_can_read_it_without_the_token(client, admin_headers):
    response = await client.get("/metrics", headers=admin_headers)
    assert response.status_code == 200
    assert PROMETHEUS_MARKER in response.text


@pytest.mark.parametrize("role", ["manager", "viewer"])
async def test_other_roles_cannot_read_it(client, admin_headers, role):
    headers = await create_user_headers(client, admin_headers, f"{role}@example.com", role)
    response = await client.get("/metrics", headers=headers)
    assert response.status_code == 403
    assert PROMETHEUS_MARKER not in response.text


async def test_a_refresh_token_is_not_an_access_token_here_either(client, admin_headers):
    login = await client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "SuperSecret123"}
    )
    response = await client.get("/metrics", headers=bearer(login.json()["refresh_token"]))
    assert response.status_code == 401


async def test_the_app_served_in_production_guards_it_too(monkeypatch, metrics_token):
    """``create_asgi_app()`` mounts the FastAPI app under the MCP connector."""
    settings = get_settings()
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_auth_enabled", False)
    monkeypatch.setattr(settings, "public_base_url", "http://testserver")
    async with AsyncClient(
        transport=ASGITransport(app=create_asgi_app()), base_url="http://test"
    ) as http:
        assert (await http.get("/metrics")).status_code == 401
        assert (await http.get("/metrics", headers=bearer(metrics_token))).status_code == 200


async def test_the_endpoint_stays_out_of_the_openapi_schema():
    assert "/metrics" not in create_app().openapi()["paths"]

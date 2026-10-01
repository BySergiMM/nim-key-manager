"""The default rate limit (RATE_LIMIT_DEFAULT) applies to every route.

slowapi evaluates ``default_limits`` only through ``SlowAPIMiddleware``, which the application
never installed: the configured ``120/minute`` limit did not exist, and only the two routes that
carry their own ``@limiter.limit`` (login and dispense) were limited at all.

Installing the stock middleware is not enough either. It finds the endpoint by iterating
``app.routes``, and since FastAPI 0.137 routers added with ``include_router`` are no longer
flattened into that list, so it skips every one of them. ``create_app`` therefore applies the
limit as an application-wide dependency (``app.api.rate_limit.enforce_default_limit``). The
tests use ``/api/v1/auth/me``, a route that comes from an included router.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app, create_asgi_app

# Anonymous, cheap and without a limit of its own: the plain "default limit" case.
UNDECORATED_ROUTE = "/api/v1/auth/me"


def default_limit() -> int:
    return int(get_settings().rate_limit_default.split("/")[0])


def from_address(app, address: str) -> AsyncClient:
    transport = ASGITransport(app=app, client=(address, 4000))
    return AsyncClient(transport=transport, base_url="http://test")


async def statuses(http: AsyncClient, path: str, count: int) -> list[int]:
    return [(await http.get(path)).status_code for _ in range(count)]


async def test_requests_over_the_default_limit_get_429(rate_limiting):
    limit = default_limit()
    async with from_address(create_app(), "203.0.113.10") as http:
        codes = await statuses(http, UNDECORATED_ROUTE, limit + 3)
        over = await http.get(UNDECORATED_ROUTE)
    assert codes == [401] * limit + [429] * 3  # 401 = reached the route, unauthenticated
    assert "Rate limit exceeded" in over.json()["error"]
    assert over.headers["x-request-id"]  # answered inside the request-context middleware


async def test_the_same_requests_are_not_limited_when_the_limiter_is_off(client):
    """Control for the test above: only ``RATE_LIMIT_ENABLED`` differs."""
    codes = await statuses(client, UNDECORATED_ROUTE, default_limit() + 3)
    assert set(codes) == {401}


async def test_the_budget_belongs_to_the_client_address(rate_limiting):
    app = create_app()
    limit = default_limit()
    async with from_address(app, "203.0.113.10") as noisy:
        assert await statuses(noisy, UNDECORATED_ROUTE, limit + 1) == [401] * limit + [429]
    async with from_address(app, "203.0.113.11") as other:
        assert (await other.get(UNDECORATED_ROUTE)).status_code == 401


async def test_the_health_probe_is_never_answered_with_429(rate_limiting):
    """The platform restarts the instance when its probe keeps failing."""
    async with from_address(create_app(), "203.0.113.10") as http:
        codes = await statuses(http, "/health", default_limit() + 5)
    assert set(codes) == {200}


async def test_the_app_served_in_production_applies_the_limit_too(rate_limiting, monkeypatch):
    """``create_asgi_app()`` mounts the FastAPI app under the MCP connector."""
    settings = get_settings()
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_auth_enabled", False)
    monkeypatch.setattr(settings, "public_base_url", "http://testserver")
    limit = default_limit()
    async with from_address(create_asgi_app(), "203.0.113.10") as http:
        codes = await statuses(http, UNDECORATED_ROUTE, limit + 2)
    assert codes == [401] * limit + [429] * 2

"""CORS is closed unless ``CORS_ORIGINS`` opens it, and a wildcard never carries credentials.

The default used to be ``["*"]`` together with ``allow_credentials=True``. Starlette answers a
credentialed request from any origin by echoing that origin back with
``Access-Control-Allow-Credentials: true``, so the "wildcard" behaved as "every website, with
credentials": the combination browsers refuse for a literal ``*``, but reflected.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.main import create_app

EVIL = "https://evil.example"
ALLOWED = "https://app.example.com"
PREFLIGHT = {
    "Access-Control-Request-Method": "GET",
    "Access-Control-Request-Headers": "authorization",
}
ROUTE = "/api/v1/auth/me"


async def cross_origin(monkeypatch, origins: list[str], origin: str):
    """A preflight and a plain request from ``origin`` to an app that allows ``origins``."""
    monkeypatch.setattr(get_settings(), "cors_origins", origins)
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        preflight = await http.options(ROUTE, headers={"Origin": origin, **PREFLIGHT})
        simple = await http.get("/health", headers={"Origin": origin})
    return preflight, simple


def grants_origin(response, origin: str) -> bool:
    return response.headers.get("access-control-allow-origin") in (origin, "*")


def test_no_origin_is_allowed_by_default():
    assert Settings.model_fields["cors_origins"].default == []


async def test_a_foreign_site_gets_no_cors_permission_by_default(monkeypatch):
    preflight, simple = await cross_origin(monkeypatch, [], EVIL)
    for response in (preflight, simple):
        assert "access-control-allow-origin" not in response.headers
    assert preflight.status_code == 400  # the browser refuses to send the real request
    assert simple.status_code == 200  # same-origin and non-browser clients are unaffected


@pytest.mark.parametrize("origins", [["*"], ["*", ALLOWED]])
async def test_a_wildcard_never_grants_credentials_to_a_foreign_site(monkeypatch, origins):
    """The attack: a page on evil.example calling the API with credentials."""
    preflight, simple = await cross_origin(monkeypatch, origins, EVIL)
    for response in (preflight, simple):
        assert response.headers.get("access-control-allow-credentials") != "true"
        assert response.headers.get("access-control-allow-origin") != EVIL  # not reflected
    assert preflight.status_code == 200  # the wildcard itself still works, without credentials


async def test_a_listed_origin_is_allowed_with_credentials(monkeypatch):
    preflight, simple = await cross_origin(monkeypatch, [ALLOWED], ALLOWED)
    for response in (preflight, simple):
        assert response.headers["access-control-allow-origin"] == ALLOWED
        assert response.headers["access-control-allow-credentials"] == "true"
    assert preflight.status_code == 200


async def test_an_unlisted_origin_is_refused_when_others_are_listed(monkeypatch):
    preflight, simple = await cross_origin(monkeypatch, [ALLOWED], EVIL)
    assert preflight.status_code == 400
    assert not grants_origin(preflight, EVIL) and not grants_origin(simple, EVIL)

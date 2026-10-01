"""The client address behind a reverse proxy cannot be chosen by the client.

The container entrypoint used to start uvicorn with ``--forwarded-allow-ips "*"``. uvicorn then
believed ``X-Forwarded-For`` from every peer and used its LEFT-most entry, which the client
writes itself, so one client could rotate the header to get a new bucket in the login limiter on
every request and could put arbitrary text in the audit log.

These tests run the real ``scripts/entrypoint.sh`` with stand-in ``alembic`` / ``uvicorn``
executables to capture the flags it passes, and then serve the application through uvicorn's own
``Config`` with exactly those flags (so uvicorn's real ``ProxyHeadersMiddleware`` decides which
client address the application sees).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from uvicorn import Config

from app.api.rate_limit import limiter
from app.core.config import get_settings
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = ROOT / "scripts" / "entrypoint.sh"

pytestmark = pytest.mark.skipif(not Path("/bin/sh").exists(), reason="needs a POSIX shell")


def run_entrypoint(tmp_path: Path, env: dict[str, str]) -> tuple[list[str], str]:
    """Run the real entrypoint; return the uvicorn arguments it would exec and its stderr."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_file = tmp_path / "uvicorn-argv.txt"
    (bin_dir / "alembic").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "uvicorn").write_text(
        '#!/bin/sh\nfor arg in "$@"; do echo "$arg"; done > "$ARGV_FILE"\n'
    )
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", str(ENTRYPOINT)],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "ARGV_FILE": str(argv_file), **env},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return argv_file.read_text().splitlines(), result.stderr


def flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def served_like_uvicorn(argv: list[str]):
    """The ASGI app as uvicorn serves it for these command-line flags."""
    config = Config(
        create_app(),
        proxy_headers="--proxy-headers" in argv,
        forwarded_allow_ips=flag_value(argv, "--forwarded-allow-ips"),
        log_config=None,
    )
    config.load()
    return config.loaded_app


def render_forwarded_allow_ips() -> str:
    text = (ROOT / "render.yaml").read_text()
    match = re.search(r'-\s+key:\s+FORWARDED_ALLOW_IPS\s*\n\s+value:\s*"([^"]+)"', text)
    assert match, "render.yaml must set FORWARDED_ALLOW_IPS for the Render deployment"
    return match.group(1)


@pytest.fixture
def rate_limiting(client):
    """Turn the rate limiter on (the test environment disables it) with clean counters."""
    previous = limiter.enabled
    limiter.enabled = True
    limiter.reset()
    yield
    limiter.enabled = previous
    limiter.reset()


# --------------------------------------------------------------------------- #
# the entrypoint                                                               #
# --------------------------------------------------------------------------- #
def test_entrypoint_trusts_only_loopback_by_default(tmp_path):
    argv, stderr = run_entrypoint(tmp_path, {})
    assert "--proxy-headers" in argv
    assert flag_value(argv, "--forwarded-allow-ips") == "127.0.0.1"
    assert "WARNING" not in stderr


def test_entrypoint_blank_variable_falls_back_to_the_safe_default(tmp_path):
    argv, _ = run_entrypoint(tmp_path, {"FORWARDED_ALLOW_IPS": ""})
    assert flag_value(argv, "--forwarded-allow-ips") == "127.0.0.1"


def test_entrypoint_takes_the_trusted_proxies_from_the_environment(tmp_path):
    argv, stderr = run_entrypoint(tmp_path, {"FORWARDED_ALLOW_IPS": "10.0.0.0/8,172.16.0.9"})
    assert flag_value(argv, "--forwarded-allow-ips") == "10.0.0.0/8,172.16.0.9"
    assert "WARNING" not in stderr


def test_entrypoint_warns_when_asked_to_trust_everybody(tmp_path):
    argv, stderr = run_entrypoint(tmp_path, {"FORWARDED_ALLOW_IPS": "*"})
    assert flag_value(argv, "--forwarded-allow-ips") == "*"  # still the operator's call
    assert "WARNING" in stderr and "forge" in stderr


def test_render_blueprint_never_trusts_everybody():
    value = render_forwarded_allow_ips()
    assert "*" not in value
    assert all("/" in item or re.fullmatch(r"[0-9a-fA-F.:]+", item) for item in value.split(","))


# --------------------------------------------------------------------------- #
# what the application sees                                                    #
# --------------------------------------------------------------------------- #
async def test_rotating_x_forwarded_for_does_not_bypass_the_login_limiter(
    rate_limiting, tmp_path
):
    app = served_like_uvicorn(run_entrypoint(tmp_path, {})[0])
    attempts = int(get_settings().rate_limit_auth.split("/")[0]) + 5
    statuses = []
    transport = ASGITransport(app=app, client=("203.0.113.50", 4000))  # not 127.0.0.1
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        for i in range(attempts):
            response = await http.post(
                "/api/v1/auth/login",
                json={"email": "nobody@example.com", "password": "wrong-password"},
                headers={"X-Forwarded-For": f"198.51.100.{i + 1}"},  # a "new client" each time
            )
            statuses.append(response.status_code)
    limit = attempts - 5
    assert statuses[:limit] == [401] * limit
    assert statuses[limit:] == [429] * 5


@pytest.mark.parametrize(
    ("peer", "forwarded_for", "expected"),
    [
        # A peer outside the trusted ranges: the header is ignored, whatever it says.
        ("203.0.113.50", "198.51.100.77", "203.0.113.50"),
        ("203.0.113.50", "<img src=x onerror=alert(1)>", "203.0.113.50"),
        # The proxy (a private address): the entry it appended is the client. Whatever the
        # client wrote to its left is ignored.
        ("10.1.2.3", "198.51.100.77", "198.51.100.77"),
        ("10.1.2.3", "1.2.3.4, 198.51.100.77", "198.51.100.77"),
        ("172.20.0.9", "<img src=x onerror=alert(1)>, 198.51.100.77, 10.9.9.9", "198.51.100.77"),
    ],
)
async def test_audit_log_records_the_address_the_proxy_saw(
    client, admin_headers, tmp_path, peer, forwarded_for, expected
):
    argv, _ = run_entrypoint(tmp_path, {"FORWARDED_ALLOW_IPS": render_forwarded_allow_ips()})
    app = served_like_uvicorn(argv)
    transport = ASGITransport(app=app, client=(peer, 4000))
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "SuperSecret123"},
            headers={"X-Forwarded-For": forwarded_for},
        )
    assert response.status_code == 200, response.text

    entries = (
        await client.get("/api/v1/audit?action=user.login&limit=50", headers=admin_headers)
    ).json()
    addresses = {entry["ip_address"] for entry in entries}
    assert expected in addresses
    assert not any("<" in (address or "") for address in addresses)

"""One version, and the notices the documentation promises.

``pyproject.toml`` said ``1.1.0`` while ``app/__init__.py`` said ``1.0.0`` (both files came from
the same commit), so the packaging metadata and what ``/health``, the OpenAPI document and the MCP
server report disagreed. ``app.__version__`` is now the only place the number is written.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import app
from app.core.config import get_settings
from app.main import create_app
from app.mcp.server import build_mcp_server

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = (ROOT / "pyproject.toml").read_text()
SEMVER = re.compile(r"\d+\.\d+\.\d+")


def project_table() -> str:
    match = re.search(r"^\[project\]\n(.*?)(?=^\[)", PYPROJECT, re.MULTILINE | re.DOTALL)
    assert match
    return match.group(1)


def test_the_version_is_written_in_one_place_only():
    assert not re.search(r"^version\s*=", project_table(), re.MULTILINE)  # no static copy
    assert re.search(r'^dynamic\s*=\s*\["version"\]', project_table(), re.MULTILINE)
    assert re.search(r'\[tool\.hatch\.version\]\s*\npath\s*=\s*"app/__init__\.py"', PYPROJECT)
    assert SEMVER.fullmatch(app.__version__)


async def test_everything_that_reports_a_version_reports_that_one(client):
    assert (await client.get("/health")).json()["version"] == app.__version__
    assert create_app().version == app.__version__
    server = build_mcp_server(get_settings().model_copy(update={"mcp_auth_enabled": False}), "")
    assert server.version == app.__version__


def test_no_other_file_pins_a_different_version():
    """The only literal version in the sources is ``app.__version__``."""
    for path in [*(ROOT / "app").rglob("*.py"), ROOT / "Dockerfile", ROOT / "render.yaml"]:
        if path.name == "__init__.py" and path.parent.name == "app":
            continue
        assert not re.search(r"""__version__\s*=\s*["']""", path.read_text()), path


@pytest.mark.parametrize("readme", ["README.md", "README.es.md"])
def test_the_readmes_say_the_project_is_not_affiliated_with_nvidia(readme):
    text = (ROOT / readme).read_text()
    assert re.search(r"(Not affiliated with NVIDIA|No afiliado con NVIDIA)", text)
    assert "NVIDIA Corporation" in text


def test_the_lru_rationale_does_not_promise_to_stretch_nvidia_rate_limits():
    text = (ROOT / "docs" / "architecture.md").read_text()
    assert "respecting NVIDIA free-tier rate limits" not in text
    assert "does not raise, bypass or enforce any NVIDIA rate limit" in text


def bounds(requirement: str) -> tuple[tuple[int, ...] | None, tuple[int, ...] | None]:
    """The (lower, upper) version bounds ``pyproject.toml`` declares for ``requirement``."""
    match = re.search(rf'^\s*"{re.escape(requirement)}(?:\[\w+\])?([^"]*)"', project_table(), re.M)
    assert match, f"{requirement} is not a dependency"

    def version(operator: str) -> tuple[int, ...] | None:
        found = re.search(rf"(?<![<>=!~]){operator}\s*(\d+(?:\.\d+)*)", match.group(1))
        return tuple(int(part) for part in found.group(1).split(".")) if found else None

    return version(">="), version("<")


def test_uvicorn_is_new_enough_for_the_cidr_ranges_the_entrypoint_passes():
    """``--forwarded-allow-ips 10.0.0.0/8`` (render.yaml) needs uvicorn 0.31.0 or later: an older
    one reads the CIDR as a literal host name and trusts no proxy at all."""
    lower, _ = bounds("uvicorn")
    assert lower is not None and lower >= (0, 31)


def test_slowapi_is_capped_below_the_release_that_may_move_its_private_api():
    """``app/api/rate_limit.py`` calls ``Limiter._check_request_limit`` and ``_should_exempt``."""
    lower, upper = bounds("slowapi")
    assert lower is not None and upper is not None and upper <= (0, 2)

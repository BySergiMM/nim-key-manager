"""Platform-aware locations for configuration, data and runtime files.

Everything the application writes lives under a single home directory so an
install can be inspected, backed up or removed in one step:

``Windows``  ``%LOCALAPPDATA%\\nim-key-manager``
``macOS``    ``~/Library/Application Support/nim-key-manager``
``Linux``    ``${XDG_DATA_HOME:-~/.local/share}/nim-key-manager``

Override with ``NIMKM_HOME``. Layout::

    <home>/config.env      # generated settings (0600)
    <home>/data/nimkm.db   # default SQLite database
    <home>/runtime/        # managed interpreter + virtualenv (installer)
    <home>/bin/            # nimkm shim (installer)
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

APP_DIR_NAME = "nim-key-manager"
HOME_ENV_VAR = "NIMKM_HOME"


def _platform_home() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
        return Path.home() / "AppData" / "Local" / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_DIR_NAME


def home() -> Path:
    """Root directory for this installation."""
    override = os.environ.get(HOME_ENV_VAR)
    return Path(override).expanduser() if override else _platform_home()


def config_file() -> Path:
    """Generated environment file read by :class:`app.core.config.Settings`."""
    return home() / "config.env"


def data_dir() -> Path:
    return home() / "data"


def logs_dir() -> Path:
    return home() / "logs"


def runtime_dir() -> Path:
    return home() / "runtime"


def bin_dir() -> Path:
    return home() / "bin"


def default_database_url() -> str:
    """SQLite URL inside the home directory (absolute, so CWD never matters)."""
    return f"sqlite+aiosqlite:///{(data_dir() / 'nimkm.db').as_posix()}"


def sqlite_path(database_url: str) -> Path | None:
    """Filesystem path backing a SQLite URL, or ``None`` for other engines."""
    if not database_url.startswith("sqlite"):
        return None
    _, _, location = database_url.partition(":///")
    location = location.split("?", 1)[0]
    if not location or location == ":memory:":
        return None
    return Path(location)


def ensure_dirs() -> Path:
    """Create the home layout with owner-only permissions and return the root."""
    root = home()
    for directory in (root, data_dir(), logs_dir()):
        directory.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        with contextlib.suppress(OSError):  # exotic filesystems
            root.chmod(0o700)
    return root

"""Discover MCP clients installed on this machine and register the server with them.

Claude Code (and Claude Desktop) declare MCP servers as JSON objects under an
``mcpServers`` key. This module locates those files and edits them.

It is written defensively, because it touches files the user did not ask us to
own and cannot afford to lose:

* **Never rewrite what cannot be parsed.** A file with comments, a truncated
  file or anything that is not a JSON object is reported and left alone.
* **Change as little as possible.** Indentation, newline style, byte-order mark,
  key order and non-ASCII characters are preserved, so registering leaves a
  one-entry diff rather than a whole-file diff.
* **Back up before every real change**, keep the last few, and never let two
  backups in the same second overwrite each other.
* **Write atomically, through symlinks**, so a crash cannot truncate the file
  and a dotfile manager's link is not replaced by a regular file.
* **Refuse a lost update.** Claude Code writes to its own configuration while it
  runs; if the file changed under us between read and write, we re-read and
  retry instead of overwriting their change.

Nothing here imports the application: it is plain file handling, so it can be
unit-tested and can run before the database exists.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# What we assume about the clients                                             #
#                                                                              #
# Both Claude Code and Claude Desktop keep MCP servers under a top-level        #
# ``mcpServers`` object, keyed by name, with ``command``/``args``/``env``.      #
# That shape is the interoperable part of the ecosystem and is what every       #
# client's own documentation asks users to paste.                               #
#                                                                              #
# The *locations* below are the part that could move in a future release. The   #
# failure mode if they do is deliberately harmless: `detected_targets()` finds  #
# nothing, `nimkm mcp setup` reports that and prints the JSON to paste, and no  #
# file is written. It never silently edits a file the client stopped reading.   #
# If a location changes, only `_claude_code_home` / `_claude_desktop_config`    #
# need updating, and `claude mcp add` remains the vendor-supported escape hatch.#
# --------------------------------------------------------------------------- #
SERVER_KEY = "mcpServers"
DEFAULT_SERVER_NAME = "nimkm"
#: Backups kept per file; older ones are pruned so $HOME does not fill up with
#: copies of a file that may contain the client's own credentials.
MAX_BACKUPS = 3
_TEMP_SUFFIX = ".nimkm-tmp"


class ClientConfigError(RuntimeError):
    """The client configuration exists but cannot be edited safely."""


@dataclass(frozen=True)
class ClientTarget:
    """A configuration file that can hold MCP server declarations."""

    key: str
    label: str
    path: Path
    restart_hint: str

    @property
    def exists(self) -> bool:
        return self.path.is_file()


@dataclass(frozen=True)
class WriteOutcome:
    """What a register/unregister call actually did."""

    changed: bool
    backup: Path | None = None


@dataclass(frozen=True)
class _Document:
    """A parsed configuration plus everything needed to write it back as it was."""

    data: dict[str, Any]
    path: Path            # symlinks resolved: the file we must actually replace
    indent: int | str | None
    newline: str
    bom: bool
    trailing_newline: bool
    stamp: tuple[int, int] | None    # (mtime_ns, size) when it was read
    mode: int | None = None          # permission bits to restore after replacing


# --------------------------------------------------------------------------- #
# discovery                                                                    #
# --------------------------------------------------------------------------- #
def _claude_code_home() -> Path:
    """Claude Code stores user-scope configuration in ``~/.claude.json``."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(override).expanduser() if override else Path.home()
    return base / ".claude.json"


def _claude_desktop_config() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        support = Path.home() / "Library" / "Application Support" / "Claude"
        return support / "claude_desktop_config.json"
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "Claude" / "claude_desktop_config.json"


def discover_targets(project_dir: Path | None = None) -> list[ClientTarget]:
    """All known client configuration files, most likely first."""
    project = project_dir or Path.cwd()
    return [
        ClientTarget(
            key="user",
            label="Claude Code (all your projects)",
            path=_claude_code_home(),
            restart_hint="restart Claude Code, or run /mcp to reconnect",
        ),
        ClientTarget(
            key="project",
            label=f"Claude Code (this project: {project.name})",
            path=project / ".mcp.json",
            restart_hint="restart Claude Code; teammates get it from version control",
        ),
        ClientTarget(
            key="desktop",
            label="Claude Desktop",
            path=_claude_desktop_config(),
            restart_hint="quit Claude Desktop completely and reopen it",
        ),
    ]


def target_by_key(key: str, project_dir: Path | None = None) -> ClientTarget:
    for target in discover_targets(project_dir):
        if target.key == key:
            return target
    raise ClientConfigError(f"unknown scope {key!r}")


def detected_targets(project_dir: Path | None = None) -> list[ClientTarget]:
    """Only the configuration files that already exist on this machine."""
    return [target for target in discover_targets(project_dir) if target.exists]


# --------------------------------------------------------------------------- #
# reading                                                                      #
# --------------------------------------------------------------------------- #
def _detect_indent(raw: str) -> int | str | None:
    """The file's own indentation, or ``None`` when it is written on one line."""
    for line in raw.splitlines()[1:]:
        stripped = line.lstrip()
        if not stripped:
            continue
        prefix = line[: len(line) - len(stripped)]
        if not prefix:
            continue
        return "\t" if prefix[0] == "\t" else len(prefix)
    return None


def _read(target: ClientTarget) -> _Document:
    """Parse the configuration, remembering how it was formatted."""
    # A symlinked config (dotfile managers do this) must be written through, not
    # replaced -- os.replace on the link would turn it into a regular file.
    path = target.path
    try:
        resolved = path.resolve()
        if resolved.exists() or path.is_symlink():
            path = resolved
    except OSError:  # pragma: no cover - unresolvable paths stay as given
        pass

    if not path.is_file():
        return _Document({}, path, 2, "\n", False, True, None)

    try:
        blob = path.read_bytes()
        stat = path.stat()
    except OSError as exc:
        raise ClientConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc

    bom = blob.startswith(b"\xef\xbb\xbf")
    raw = blob.decode("utf-8-sig")
    stamp = (stat.st_mtime_ns, stat.st_size)
    # os.replace hands the destination the *temporary* file's permissions, so a
    # config the user restricted to 0600 would silently widen to the umask
    # default. These clients keep their own credentials in here.
    mode = stat.st_mode & 0o777
    newline = "\r\n" if "\r\n" in raw else "\n"
    trailing = raw.endswith(("\n", "\r"))

    if not raw.strip():
        return _Document({}, path, 2, newline, bom, True, stamp, mode)

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        hint = ""
        if raw.lstrip().startswith(("//", "/*", "#")):
            hint = " It looks like it contains comments, which JSON does not allow."
        raise ClientConfigError(
            f"{path} is not valid JSON ({exc.msg} at line {exc.lineno}).{hint} "
            "Fix or move the file; refusing to overwrite it."
        ) from exc
    if not isinstance(document, dict):
        raise ClientConfigError(
            f"{path} contains a JSON {type(document).__name__}, not an object; "
            "refusing to overwrite it."
        )
    return _Document(document, path, _detect_indent(raw), newline, bom, trailing, stamp, mode)


def load(target: ClientTarget) -> dict[str, Any]:
    """The parsed configuration, or an empty document when absent."""
    return _read(target).data


def registered_servers(target: ClientTarget) -> dict[str, Any]:
    servers = load(target).get(SERVER_KEY)
    return servers if isinstance(servers, dict) else {}


def registration(target: ClientTarget, name: str = DEFAULT_SERVER_NAME) -> dict[str, Any] | None:
    entry = registered_servers(target).get(name)
    return entry if isinstance(entry, dict) else None


# --------------------------------------------------------------------------- #
# writing                                                                      #
# --------------------------------------------------------------------------- #
def _serialise(document: _Document, data: dict[str, Any]) -> bytes:
    """Render ``data`` the way the file was already written."""
    if document.indent is None:   # the file was minified; keep it that way
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        body = json.dumps(data, ensure_ascii=False, indent=document.indent)
    if document.trailing_newline:
        body += "\n"
    if document.newline != "\n":
        body = body.replace("\n", document.newline)
    blob = body.encode("utf-8")
    return b"\xef\xbb\xbf" + blob if document.bom else blob


def backup(path: Path) -> Path | None:
    """Copy ``path`` next to itself with a timestamp, pruning older copies."""
    if not path.is_file():
        return None
    # Microseconds: two edits in the same second must not clobber each other.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, destination)
    _prune_backups(path)
    return destination


def _prune_backups(path: Path) -> None:
    existing = sorted(path.parent.glob(f"{path.name}.bak-*"))
    for stale in existing[:-MAX_BACKUPS]:
        with contextlib.suppress(OSError):  # best effort
            stale.unlink()


class _ConcurrentChange(Exception):
    """The file changed between reading it and replacing it."""


def _stamp_of(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _write(document: _Document, blob: bytes) -> None:
    """Replace the file atomically, or explain why it cannot be done."""
    path = document.path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + _TEMP_SUFFIX)
        with open(temporary, "wb") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        if document.mode is not None:
            with contextlib.suppress(OSError):
                os.chmod(temporary, document.mode)
        try:
            # Last possible moment to notice the client rewrote its own file.
            # The window between this check and the replace is microseconds; it
            # cannot be closed entirely without a lock the clients do not take.
            if document.stamp is not None and _stamp_of(path) != document.stamp:
                temporary.unlink(missing_ok=True)
                raise _ConcurrentChange
            os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
    except PermissionError as exc:
        raise ClientConfigError(
            f"no permission to write {path}. Close the MCP client if it is running, "
            f"check the file is not read-only, then try again ({exc.strerror or exc})."
        ) from exc
    except OSError as exc:
        raise ClientConfigError(f"could not write {path}: {exc.strerror or exc}") from exc


def _apply(
    target: ClientTarget,
    mutate: Callable[[dict[str, Any]], dict[str, Any]],
    attempts: int = 3,
) -> WriteOutcome:
    """Read, mutate and write back, refusing to clobber a concurrent change."""
    for _attempt in range(attempts):
        document = _read(target)
        # `document.data` is a fresh parse owned by this call, so mutating it
        # in place is safe and avoids copying a multi-megabyte configuration.
        blob = _serialise(document, mutate(document.data))

        current = document.path.read_bytes() if document.path.is_file() else None
        if current == blob:
            return WriteOutcome(changed=False)

        if document.stamp is not None and _stamp_of(document.path) != document.stamp:
            # The client rewrote its own file while we were reading it. Writing
            # now would discard their change; start over from the new content.
            continue

        saved = backup(document.path)
        try:
            _write(document, blob)
        except _ConcurrentChange:
            if saved is not None:
                saved.unlink(missing_ok=True)
            continue
        return WriteOutcome(changed=True, backup=saved)

    raise ClientConfigError(
        f"{target.path} keeps changing while we edit it. Close the MCP client "
        "and run this again."
    )


def register(
    target: ClientTarget, entry: dict[str, Any], name: str = DEFAULT_SERVER_NAME
) -> WriteOutcome:
    """Add or replace the server declaration, preserving every other key."""

    def mutate(document: dict[str, Any]) -> dict[str, Any]:
        servers = document.get(SERVER_KEY)
        if not isinstance(servers, dict):
            servers = {}
        servers[name] = entry
        document[SERVER_KEY] = servers
        return document

    return _apply(target, mutate)


def unregister(target: ClientTarget, name: str = DEFAULT_SERVER_NAME) -> WriteOutcome:
    """Remove the server declaration if it is there."""
    if registration(target, name) is None and name not in registered_servers(target):
        return WriteOutcome(changed=False)

    def mutate(document: dict[str, Any]) -> dict[str, Any]:
        servers = document.get(SERVER_KEY)
        if isinstance(servers, dict):
            servers.pop(name, None)
            document[SERVER_KEY] = servers
        return document

    return _apply(target, mutate)


# --------------------------------------------------------------------------- #
# the entry itself                                                             #
# --------------------------------------------------------------------------- #
def launcher() -> str:
    """Absolute path to the ``nimkm`` executable a client should run.

    An absolute path is a requirement, not a nicety: MCP clients are launched
    from desktop sessions that never sourced a shell profile, so ``PATH`` may
    not contain the launcher -- and a ``PATH``-resolved name is whatever happens
    to be first when the client starts, which is not a decision to leave open in
    a file that gets executed.
    """
    from app.core import paths

    scripts = Path(sys.executable).parent
    candidates = [
        scripts / ("nimkm.exe" if os.name == "nt" else "nimkm"),
        paths.bin_dir() / ("nimkm.cmd" if os.name == "nt" else "nimkm"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("nimkm")
    if found:
        return str(Path(found).resolve())
    raise ClientConfigError(
        "cannot find the nimkm executable to register. Reinstall with the official "
        "installer, or pass the path yourself with --command."
    )


def server_entry(command: str | None = None) -> dict[str, Any]:
    """The JSON declaration clients need in order to start the server."""
    from app.core import paths

    return {
        "command": command or launcher(),
        "args": ["mcp", "serve"],
        "env": {"NIMKM_HOME": str(paths.home())},
    }


def snippet(name: str = DEFAULT_SERVER_NAME, command: str | None = None) -> str:
    """Copy-pasteable JSON for clients this tool does not know how to edit."""
    return json.dumps(
        {SERVER_KEY: {name: server_entry(command)}}, indent=2, ensure_ascii=False
    )

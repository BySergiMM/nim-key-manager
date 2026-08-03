"""Registering the MCP server with local clients (Claude Code, Claude Desktop).

These tests guard user data. The configuration files belong to the client, not
to us: they hold unrelated state, they are written by a program that may be
running right now, and a user may have formatted or symlinked them any way they
like. The contract is "add one entry, change nothing else, never lose data" --
and every clause of it is asserted here.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from app import cli
from app.core import paths
from app.mcp import clients

EXISTING_CONFIG = {
    "numStartups": 42,
    "theme": "dark",
    "projects": {"/work/app": {"allowedTools": []}},
    "mcpServers": {"other": {"command": "node", "args": ["server.js"]}},
}
ENTRY = {"command": "/opt/nimkm", "args": ["mcp", "serve"], "env": {"NIMKM_HOME": "/home/u"}}


def make_target(path: Path) -> clients.ClientTarget:
    return clients.ClientTarget("user", "Claude Code (test)", path, "restart it")


@pytest.fixture
def target(tmp_path):
    path = tmp_path / ".claude.json"
    path.write_text(json.dumps(EXISTING_CONFIG, indent=2), encoding="utf-8")
    return make_target(path)


# --------------------------------------------------------------------------- #
# discovery                                                                    #
# --------------------------------------------------------------------------- #
def test_discovery_honours_claude_config_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert clients.target_by_key("user").path == tmp_path / ".claude.json"


def test_project_scope_lives_next_to_the_code(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    project = clients.target_by_key("project", project_dir=tmp_path / "repo")
    assert project.path == tmp_path / "repo" / ".mcp.json"


def test_detected_targets_only_lists_existing_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(clients, "_claude_desktop_config", lambda: tmp_path / "absent.json")
    assert clients.detected_targets(project_dir=tmp_path) == []
    (tmp_path / ".claude.json").write_text("{}", encoding="utf-8")
    assert [t.key for t in clients.detected_targets(project_dir=tmp_path)] == ["user"]


def test_desktop_path_is_platform_appropriate() -> None:
    path = clients._claude_desktop_config()
    assert path.name == "claude_desktop_config.json"
    assert path.parent.name == "Claude"
    if sys.platform == "win32":
        assert "Roaming" in str(path) or "AppData" in str(path)


def test_unknown_scope_is_rejected() -> None:
    with pytest.raises(clients.ClientConfigError):
        clients.target_by_key("nope")


# --------------------------------------------------------------------------- #
# reading: never destroy what cannot be understood                             #
# --------------------------------------------------------------------------- #
def test_missing_file_reads_as_empty(tmp_path) -> None:
    absent = make_target(tmp_path / "none.json")
    assert clients.load(absent) == {}
    assert clients.registered_servers(absent) == {}
    assert clients.registration(absent) is None


def test_empty_file_reads_as_empty(tmp_path) -> None:
    path = tmp_path / "empty.json"
    path.write_text("   \n", encoding="utf-8")
    assert clients.load(make_target(path)) == {}


def test_utf8_bom_is_tolerated(tmp_path) -> None:
    path = tmp_path / "bom.json"
    path.write_bytes('﻿{"mcpServers": {"a": {"command": "x"}}}'.encode())
    assert clients.registered_servers(make_target(path)) == {"a": {"command": "x"}}


def test_invalid_json_is_never_overwritten(tmp_path) -> None:
    path = tmp_path / "broken.json"
    original = '{"mcpServers": {,,,}'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(clients.ClientConfigError, match="not valid JSON"):
        clients.register(make_target(path), ENTRY)
    assert path.read_text(encoding="utf-8") == original


def test_json_with_comments_is_refused_with_a_hint(tmp_path) -> None:
    """A user who added // comments must be told why, not have them deleted."""
    path = tmp_path / "commented.json"
    original = '// my client config\n{"mcpServers": {}}'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(clients.ClientConfigError, match="comments"):
        clients.register(make_target(path), ENTRY)
    assert path.read_text(encoding="utf-8") == original


def test_non_object_document_is_rejected(tmp_path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(clients.ClientConfigError, match="not an object"):
        clients.load(make_target(path))


def test_truncated_file_is_refused(tmp_path) -> None:
    path = tmp_path / "cut.json"
    path.write_text('{"mcpServers": {"a": {"comm', encoding="utf-8")
    with pytest.raises(clients.ClientConfigError):
        clients.register(make_target(path), ENTRY)


# --------------------------------------------------------------------------- #
# writing: change one entry, nothing else                                      #
# --------------------------------------------------------------------------- #
def test_register_preserves_everything_else(target) -> None:
    outcome = clients.register(target, ENTRY)

    document = json.loads(target.path.read_text(encoding="utf-8"))
    assert document["numStartups"] == 42
    assert document["theme"] == "dark"
    assert document["projects"] == EXISTING_CONFIG["projects"]
    assert document["mcpServers"]["other"] == EXISTING_CONFIG["mcpServers"]["other"]
    assert document["mcpServers"]["nimkm"] == ENTRY

    assert outcome.changed is True
    assert outcome.backup is not None and outcome.backup.exists()
    assert json.loads(outcome.backup.read_text(encoding="utf-8")) == EXISTING_CONFIG


def test_foreign_key_order_is_preserved(tmp_path) -> None:
    path = tmp_path / "ordered.json"
    path.write_text(json.dumps({"z": 1, "a": 2, "mcpServers": {}}, indent=2), encoding="utf-8")
    clients.register(make_target(path), ENTRY)
    assert list(json.loads(path.read_text(encoding="utf-8")))[:2] == ["z", "a"]


def test_running_it_ten_times_changes_nothing(target) -> None:
    """The headline promise: `nimkm mcp setup` is safe to repeat forever."""
    clients.register(target, ENTRY)
    settled = target.path.read_bytes()
    backups_before = len(list(target.path.parent.glob("*.bak-*")))

    outcomes = [clients.register(target, ENTRY) for _ in range(10)]

    assert target.path.read_bytes() == settled, "a repeat run rewrote the file"
    assert not any(outcome.changed for outcome in outcomes)
    assert not any(outcome.backup for outcome in outcomes)
    assert len(list(target.path.parent.glob("*.bak-*"))) == backups_before
    servers = json.loads(settled)["mcpServers"]
    assert list(servers) == ["other", "nimkm"], "entry duplicated or reordered"


def test_register_replaces_a_stale_entry(target) -> None:
    clients.register(target, {"command": "/old/nimkm", "args": ["mcp", "serve"]})
    clients.register(target, ENTRY)
    assert clients.registration(target) == ENTRY


def test_register_creates_a_missing_file(tmp_path) -> None:
    fresh = make_target(tmp_path / "nested" / ".mcp.json")
    outcome = clients.register(fresh, ENTRY)
    assert outcome.changed is True and outcome.backup is None
    assert clients.registration(fresh) == ENTRY


def test_register_under_a_custom_name(target) -> None:
    clients.register(target, ENTRY, name="nvidia-keys")
    assert clients.registration(target, "nvidia-keys") is not None
    assert clients.registration(target, "nimkm") is None


def test_register_repairs_a_non_object_servers_key(tmp_path) -> None:
    path = tmp_path / "odd.json"
    path.write_text('{"mcpServers": "oops"}', encoding="utf-8")
    clients.register(make_target(path), ENTRY)
    assert clients.registration(make_target(path)) == ENTRY


def test_register_replaces_a_non_object_entry_of_ours(tmp_path) -> None:
    path = tmp_path / "weird-entry.json"
    path.write_text('{"mcpServers": {"nimkm": "not an object"}}', encoding="utf-8")
    target = make_target(path)
    assert clients.registration(target) is None      # unusable, treated as absent
    clients.register(target, ENTRY)
    assert clients.registration(target) == ENTRY


def test_unregister_removes_a_non_object_entry_of_ours(tmp_path) -> None:
    path = tmp_path / "weird-remove.json"
    path.write_text('{"mcpServers": {"nimkm": 42, "other": {"command": "x"}}}', encoding="utf-8")
    outcome = clients.unregister(make_target(path))
    assert outcome.changed is True
    assert list(clients.registered_servers(make_target(path))) == ["other"]


def test_adds_the_key_when_absent_without_touching_the_rest(tmp_path) -> None:
    path = tmp_path / "nokey.json"
    path.write_text('{"theme": "dark"}', encoding="utf-8")
    clients.register(make_target(path), ENTRY)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["theme"] == "dark" and "nimkm" in document["mcpServers"]


def test_unregister_removes_only_our_entry(target) -> None:
    clients.register(target, ENTRY)
    outcome = clients.unregister(target)
    assert outcome.changed is True and outcome.backup is not None
    assert list(clients.registered_servers(target)) == ["other"]


def test_unregister_when_absent_is_a_no_op(target) -> None:
    outcome = clients.unregister(target)
    assert outcome.changed is False and outcome.backup is None
    assert json.loads(target.path.read_text(encoding="utf-8")) == EXISTING_CONFIG


def test_register_then_unregister_restores_the_original_bytes(tmp_path) -> None:
    path = tmp_path / "roundtrip.json"
    original = json.dumps(EXISTING_CONFIG, indent=2) + "\n"
    path.write_text(original, encoding="utf-8")
    clients.register(make_target(path), ENTRY)
    clients.unregister(make_target(path))
    assert path.read_text(encoding="utf-8") == original


def test_no_temporary_file_is_left_behind(target) -> None:
    clients.register(target, ENTRY)
    assert not list(target.path.parent.glob("*" + clients._TEMP_SUFFIX))


# --------------------------------------------------------------------------- #
# formatting: leave a one-entry diff, not a whole-file diff                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("indent", [2, 4, 8])
def test_indentation_is_preserved(tmp_path, indent) -> None:
    path = tmp_path / f"indent{indent}.json"
    path.write_text(json.dumps({"a": 1, "mcpServers": {}}, indent=indent), encoding="utf-8")
    clients.register(make_target(path), ENTRY)
    assert f'\n{" " * indent}"a"' in path.read_text(encoding="utf-8")


def test_tab_indentation_is_preserved(tmp_path) -> None:
    path = tmp_path / "tabs.json"
    path.write_text('{\n\t"a": 1,\n\t"mcpServers": {}\n}', encoding="utf-8")
    clients.register(make_target(path), ENTRY)
    assert '\n\t"a"' in path.read_text(encoding="utf-8")


def test_a_minified_file_stays_minified(tmp_path) -> None:
    path = tmp_path / "minified.json"
    path.write_text('{"a":1,"mcpServers":{}}', encoding="utf-8")
    clients.register(make_target(path), ENTRY)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_unicode_is_not_escaped(tmp_path) -> None:
    """A config with non-ASCII paths must stay readable to its owner."""
    path = tmp_path / "unicode.json"
    path.write_text(
        json.dumps({"proyecto": "café ñandú 日本語", "mcpServers": {}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    clients.register(make_target(path), ENTRY)
    assert "café ñandú 日本語" in path.read_text(encoding="utf-8")


def test_bom_is_preserved(tmp_path) -> None:
    path = tmp_path / "bom.json"
    path.write_bytes('﻿{"mcpServers": {}}'.encode())
    clients.register(make_target(path), ENTRY)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_crlf_is_preserved(tmp_path) -> None:
    path = tmp_path / "crlf.json"
    path.write_bytes(json.dumps({"mcpServers": {}}, indent=2).replace("\n", "\r\n").encode())
    blob = (clients.register(make_target(path), ENTRY), path.read_bytes())[1]
    assert blob.count(b"\r\n") == blob.count(b"\n"), "CRLF file gained bare LF endings"


def test_lf_is_not_converted_to_crlf(tmp_path) -> None:
    path = tmp_path / "lf.json"
    path.write_bytes(json.dumps({"mcpServers": {}}, indent=2).encode())
    clients.register(make_target(path), ENTRY)
    assert b"\r\n" not in path.read_bytes()


def test_absent_trailing_newline_stays_absent(tmp_path) -> None:
    path = tmp_path / "notrail.json"
    path.write_bytes(b'{"mcpServers":{}}')
    clients.register(make_target(path), ENTRY)
    assert not path.read_bytes().endswith(b"\n")


# --------------------------------------------------------------------------- #
# hostile filesystems                                                          #
# --------------------------------------------------------------------------- #
def test_unicode_and_spaces_in_the_path(tmp_path) -> None:
    path = tmp_path / "café proyecto ñ" / "sub dir" / ".mcp.json"
    clients.register(make_target(path), ENTRY)
    assert clients.registration(make_target(path)) == ENTRY


@pytest.mark.skipif(os.name == "nt", reason="chmod read-only is advisory on Windows CI")
def test_read_only_file_reports_an_actionable_error(tmp_path) -> None:
    path = tmp_path / "readonly.json"
    path.write_text('{"mcpServers": {}}', encoding="utf-8")
    os.chmod(path, stat.S_IRUSR)
    try:
        with pytest.raises(clients.ClientConfigError, match="permission"):
            clients.register(make_target(path), ENTRY)
    finally:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    assert json.loads(path.read_text(encoding="utf-8")) == {"mcpServers": {}}


def test_a_locked_file_reports_an_actionable_error(tmp_path, monkeypatch) -> None:
    """Windows refuses os.replace while another process holds the file open."""
    path = tmp_path / "locked.json"
    path.write_text('{"mcpServers": {}}', encoding="utf-8")

    def denied(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(clients.os, "replace", denied)
    with pytest.raises(clients.ClientConfigError, match="Close the MCP client"):
        clients.register(make_target(path), ENTRY)
    assert json.loads(path.read_text(encoding="utf-8")) == {"mcpServers": {}}
    assert not list(tmp_path.glob("*" + clients._TEMP_SUFFIX)), "temp file left behind"


def test_a_symlinked_config_is_written_through(tmp_path) -> None:
    """Dotfile managers symlink these files; replacing the link breaks them."""
    real = tmp_path / "real.json"
    real.write_text('{"mcpServers": {}}', encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("this platform does not allow creating symlinks here")

    clients.register(make_target(link), ENTRY)
    assert link.is_symlink(), "the symlink was replaced by a regular file"
    assert "nimkm" in json.loads(real.read_text(encoding="utf-8"))["mcpServers"]
    assert not list(tmp_path.glob("link.json.bak-*")), "backup should follow the real file"


def test_a_large_configuration_is_handled_whole(tmp_path) -> None:
    path = tmp_path / "big.json"
    big = {
        "projects": {f"/repo/{i}": {"history": ["x" * 200] * 20} for i in range(200)},
        "mcpServers": {f"srv{i}": {"command": f"c{i}"} for i in range(60)},
    }
    path.write_text(json.dumps(big, indent=2), encoding="utf-8")
    clients.register(make_target(path), ENTRY)
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert len(reloaded["mcpServers"]) == 61
    assert len(reloaded["projects"]) == 200


# --------------------------------------------------------------------------- #
# backups                                                                      #
# --------------------------------------------------------------------------- #
def test_two_changes_in_the_same_second_keep_both_backups(tmp_path) -> None:
    path = tmp_path / "fast.json"
    path.write_text('{"mcpServers": {}}', encoding="utf-8")
    clients.register(make_target(path), {"command": "a"})
    clients.register(make_target(path), {"command": "b"})
    assert len(list(tmp_path.glob("fast.json.bak-*"))) == 2


def test_backups_are_pruned(tmp_path) -> None:
    """A file that may hold the client's own credentials must not be copied forever."""
    path = tmp_path / "churn.json"
    path.write_text('{"mcpServers": {}}', encoding="utf-8")
    for i in range(10):
        clients.register(make_target(path), {"command": f"nimkm-{i}"})
    backups = list(tmp_path.glob("churn.json.bak-*"))
    assert len(backups) == clients.MAX_BACKUPS
    # The most recent ones are the ones kept.
    newest = sorted(backups)[-1]
    assert json.loads(newest.read_text(encoding="utf-8"))["mcpServers"]["nimkm"] == {
        "command": "nimkm-8"
    }


def test_backup_of_a_missing_file_is_none(tmp_path) -> None:
    assert clients.backup(tmp_path / "nothing.json") is None


# --------------------------------------------------------------------------- #
# permissions                                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX permission bits")
def test_restrictive_permissions_survive_the_write(tmp_path) -> None:
    """os.replace would otherwise widen a 0600 config to the umask default."""
    path = tmp_path / "private.json"
    path.write_text('{"mcpServers": {}}', encoding="utf-8")
    os.chmod(path, 0o600)
    clients.register(make_target(path), ENTRY)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX permission bits")
def test_backups_inherit_the_original_permissions(tmp_path) -> None:
    path = tmp_path / "private2.json"
    path.write_text('{"mcpServers": {}}', encoding="utf-8")
    os.chmod(path, 0o600)
    outcome = clients.register(make_target(path), ENTRY)
    assert outcome.backup is not None
    assert stat.S_IMODE(outcome.backup.stat().st_mode) == 0o600


def test_the_original_mode_is_carried_to_the_replacement(tmp_path, monkeypatch) -> None:
    """Platform-independent check that the mode is applied before replacing."""
    path = tmp_path / "moded.json"
    path.write_text('{"mcpServers": {}}', encoding="utf-8")
    applied: list[int] = []
    real_chmod = os.chmod

    def spy(target, mode, *args, **kwargs):
        if str(target).endswith(clients._TEMP_SUFFIX):
            applied.append(mode)
        return real_chmod(target, mode, *args, **kwargs)

    monkeypatch.setattr(clients.os, "chmod", spy)
    clients.register(make_target(path), ENTRY)
    assert applied == [stat.S_IMODE(path.stat().st_mode)]


# --------------------------------------------------------------------------- #
# concurrency: the client writes to this file while it runs                    #
# --------------------------------------------------------------------------- #
def test_a_concurrent_client_write_is_not_lost(tmp_path, monkeypatch) -> None:
    path = tmp_path / "race.json"
    path.write_text(json.dumps({"numStartups": 1, "mcpServers": {}}, indent=2), encoding="utf-8")

    real_backup = clients.backup
    calls = {"n": 0}

    def racing_backup(target_path: Path):
        """Stand in for Claude Code rewriting its own file mid-edit."""
        calls["n"] += 1
        if calls["n"] == 1:
            document = json.loads(target_path.read_text(encoding="utf-8"))
            document["numStartups"] = 99
            target_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        return real_backup(target_path)

    monkeypatch.setattr(clients, "backup", racing_backup)
    clients.register(make_target(path), ENTRY)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["numStartups"] == 99, "the client's own write was discarded"
    assert "nimkm" in document["mcpServers"], "our entry did not land"


def test_giving_up_on_a_file_that_never_settles(tmp_path, monkeypatch) -> None:
    path = tmp_path / "never.json"
    path.write_text('{"mcpServers": {}}', encoding="utf-8")

    def always_racing(target_path: Path):
        target_path.write_text(
            json.dumps({"n": os.urandom(4).hex(), "mcpServers": {}}), encoding="utf-8"
        )
        return None

    monkeypatch.setattr(clients, "backup", always_racing)
    with pytest.raises(clients.ClientConfigError, match="keeps changing"):
        clients.register(make_target(path), ENTRY)


# --------------------------------------------------------------------------- #
# the entry itself                                                             #
# --------------------------------------------------------------------------- #
def test_server_entry_points_at_this_install(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    entry = clients.server_entry()
    assert entry["args"] == ["mcp", "serve"]
    assert entry["env"]["NIMKM_HOME"] == str(tmp_path)
    assert Path(entry["command"]).is_absolute()


def test_launcher_is_always_absolute(monkeypatch) -> None:
    """A PATH-resolved name in a config file is whatever runs first at spawn time."""
    monkeypatch.setattr(clients.Path, "is_file", lambda self: False)
    monkeypatch.setattr(clients.shutil, "which", lambda name: None)
    with pytest.raises(clients.ClientConfigError, match="cannot find"):
        clients.launcher()


def test_launcher_resolves_a_path_lookup(monkeypatch, tmp_path) -> None:
    found = tmp_path / "nimkm"
    found.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(clients.Path, "is_file", lambda self: False)
    monkeypatch.setattr(clients.shutil, "which", lambda name: str(found))
    assert Path(clients.launcher()).is_absolute()


def test_snippet_is_valid_json_for_manual_setup() -> None:
    document = json.loads(clients.snippet(command="/opt/nimkm"))
    assert document["mcpServers"]["nimkm"]["command"] == "/opt/nimkm"


# --------------------------------------------------------------------------- #
# the CLI commands on top                                                      #
# --------------------------------------------------------------------------- #
@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    """A machine where only Claude Code (user scope) is installed."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path / "install"))
    monkeypatch.setattr(clients, "_claude_desktop_config", lambda: tmp_path / "absent.json")
    monkeypatch.chdir(tmp_path)
    config = tmp_path / ".claude.json"
    config.write_text(json.dumps(EXISTING_CONFIG, indent=2), encoding="utf-8")
    return config


def test_setup_registers_after_confirmation(claude_home, capsys) -> None:
    assert cli.main(["mcp", "setup", "--yes"]) == 0
    document = json.loads(claude_home.read_text(encoding="utf-8"))
    assert "nimkm" in document["mcpServers"]
    output = capsys.readouterr().out
    assert "backup:" in output and "restart" in output.lower()


def test_setup_alias_works_the_same(claude_home) -> None:
    assert cli.main(["setup", "--yes"]) == 0
    assert "nimkm" in json.loads(claude_home.read_text(encoding="utf-8"))["mcpServers"]


def test_setup_ten_times_from_the_cli_is_stable(claude_home) -> None:
    assert cli.main(["mcp", "setup", "--yes"]) == 0
    settled = claude_home.read_bytes()
    for _ in range(9):
        assert cli.main(["mcp", "setup", "--yes"]) == 0
    assert claude_home.read_bytes() == settled


def test_setup_declines_without_a_terminal(claude_home, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    # Exit 2, not 0: the installer must keep telling the user to run it.
    assert cli.main(["mcp", "setup"]) == cli.NOTHING_CHANGED
    assert "nimkm" not in json.loads(claude_home.read_text(encoding="utf-8"))["mcpServers"]
    assert "--yes" in capsys.readouterr().out


def test_setup_survives_a_prompt_that_cannot_be_answered(claude_home, monkeypatch) -> None:
    """isatty() can lie; input() then raises EOFError. That must not crash."""
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    def eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli.main(["mcp", "setup"]) == cli.NOTHING_CHANGED


def test_setup_reports_success_when_already_connected(claude_home) -> None:
    assert cli.main(["mcp", "setup", "--yes"]) == 0
    assert cli.main(["mcp", "setup", "--yes"]) == 0


def test_setup_accepts_an_interactive_yes(claude_home, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")   # bare Enter = default yes
    assert cli.main(["mcp", "setup"]) == 0
    assert "nimkm" in json.loads(claude_home.read_text(encoding="utf-8"))["mcpServers"]


def test_setup_respects_an_interactive_no(claude_home, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert cli.main(["mcp", "setup"]) == cli.NOTHING_CHANGED
    assert "nimkm" not in json.loads(claude_home.read_text(encoding="utf-8"))["mcpServers"]


def test_setup_shows_what_it_would_replace(claude_home, monkeypatch, capsys) -> None:
    """A hand-edited entry must not be swapped out silently."""
    clients.register(clients.target_by_key("user"), {"command": "/custom/nimkm"})
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    cli.main(["mcp", "setup"])
    output = capsys.readouterr().out
    # The old and the new command are both shown before anything is replaced.
    assert "/custom/nimkm" in output and "will be:" in output
    assert "skipped" in output


def test_setup_print_only_touches_nothing(claude_home, capsys) -> None:
    before = claude_home.read_bytes()
    assert cli.main(["mcp", "setup", "--print"]) == 0
    assert claude_home.read_bytes() == before
    assert json.loads(capsys.readouterr().out)["mcpServers"]["nimkm"]["args"] == ["mcp", "serve"]


def test_setup_without_any_client_explains_how(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path / "install"))
    monkeypatch.setattr(clients, "_claude_desktop_config", lambda: tmp_path / "absent.json")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["mcp", "setup"]) == 1
    output = capsys.readouterr().out
    assert "no MCP client configuration found" in output and '"mcpServers"' in output


def test_setup_reports_a_broken_config_without_dying(claude_home, capsys) -> None:
    claude_home.write_text("{ broken", encoding="utf-8")
    assert cli.main(["mcp", "setup", "--yes"]) == 1
    assert "not valid JSON" in capsys.readouterr().err
    assert claude_home.read_text(encoding="utf-8") == "{ broken"


def test_status_lists_registration_state(claude_home, capsys) -> None:
    cli.main(["mcp", "setup", "--yes"])
    capsys.readouterr()
    assert cli.main(["mcp", "status"]) == 0
    output = capsys.readouterr().out
    assert "registered" in output and str(claude_home) in output


def test_status_warns_when_the_registered_entry_is_a_different_install(claude_home, capsys) -> None:
    clients.register(clients.target_by_key("user"), {"command": "/elsewhere/nimkm"})
    cli.main(["mcp", "status"])
    assert "differs from this install" in capsys.readouterr().out


def test_status_warns_when_another_install_is_registered(claude_home, capsys) -> None:
    clients.register(clients.target_by_key("user"),
                     {"command": "/somewhere/else/nimkm", "args": ["mcp", "serve"]})
    assert cli.main(["mcp", "status"]) == 0
    assert "differs from this install" in capsys.readouterr().out


def test_status_survives_a_broken_config(claude_home, capsys) -> None:
    claude_home.write_text("{ broken", encoding="utf-8")
    # Reports rather than crashing; exit 2 because nothing ended up registered.
    assert cli.main(["mcp", "status"]) == cli.NOTHING_CHANGED
    assert "unreadable" in capsys.readouterr().out


def test_remove_unregisters(claude_home) -> None:
    cli.main(["mcp", "setup", "--yes"])
    assert cli.main(["mcp", "remove", "--yes"]) == 0
    assert "nimkm" not in json.loads(claude_home.read_text(encoding="utf-8"))["mcpServers"]


def test_remove_reports_when_nothing_is_registered(claude_home) -> None:
    assert cli.main(["mcp", "remove", "--yes"]) == 1


def test_remove_reports_a_broken_config(claude_home, capsys) -> None:
    claude_home.write_text("{ broken", encoding="utf-8")
    assert cli.main(["mcp", "remove", "--yes"]) == 1
    assert "not valid JSON" in capsys.readouterr().err


def _collect_client_checks() -> list[tuple[str, str, str]]:
    checks: list[tuple[str, str, str]] = []

    def add(level: str, subject: str, detail: str = "") -> None:
        checks.append((level, subject, detail))

    cli._add_client_checks(add)
    return checks


def test_doctor_flags_an_unregistered_client(claude_home) -> None:
    level, _subject, detail = _collect_client_checks()[0]
    assert level == "warn" and "nimkm mcp setup" in detail


def test_doctor_confirms_a_registered_client(claude_home) -> None:
    cli.main(["mcp", "setup", "--yes"])
    level, subject, _detail = _collect_client_checks()[0]
    assert level == "ok" and "Claude Code" in subject


def test_doctor_reports_an_unreadable_client_config(claude_home) -> None:
    claude_home.write_text("{ broken", encoding="utf-8")
    levels = {level for level, _s, _d in _collect_client_checks()}
    assert "warn" in levels

# Installation redesign

An audit of how NIM Key Manager was installed, what was wrong with it, what was built
instead, and what I would do differently at a much larger scale.

**Summary:** connecting this to Claude Code went from *"fork the repo, sign up for Render,
provision a database, create a GitHub OAuth app, set five environment variables, add a
custom connector by URL"* to:

```bash
curl -fsSL https://raw.githubusercontent.com/BySergiMM/nim-key-manager/main/install.sh | sh
# → "Register 'nimkm' here? [Y/n]"  → restart Claude Code → ask it for a key
```

Along the way the audit uncovered a defect that made **the documented deployment path
crash on startup**, a security footgun that could have encrypted real API keys with a
publicly known key, and — once the MCP server became the product — the fact that its
**only transport was the one local clients cannot use**.

---

## 1. What this project actually is

This mattered more than anything else in the design, and the answer changed once during
the work.

**First pass.** The brief asked for a single-command install that downloads "the right
binary" from GitHub Releases, like `uv`, `rustup` or `gh`. But this is not a CLI utility
written in Rust — it is a Python service: FastAPI + SQLAlchemy(async) + Alembic +
APScheduler + a FastMCP server + a dashboard, backed by SQLite or PostgreSQL. Copying the
static-binary shape (PyInstaller per OS/arch) would mean 6 build targets, ~80 MB
artifacts, slow cold starts and fragile hidden-import hooks for `cryptography`, `asyncpg`
and `fastmcp`. The goal was kept and the mechanism changed: one command still fetches a
versioned artifact from GitHub Releases — a wheel — and the runtime is a private
interpreter fetched by `uv`. Section 5 justifies that against the alternatives.

**Second pass (this revision).** The primary use is as an **MCP server for Claude Code**,
not as a web app you visit. That inverts the product:

- The thing to install is a *command a client spawns*, not a service you keep running.
- The install is not finished when the program is on disk — it is finished when the
  **client is registered and the tools appear in Claude**.
- The dashboard is a secondary, optional administration surface.

So the deliverable became: `curl … | sh` → "Register with Claude Code? [Y/n]" → restart
Claude → ask it for a key. Section 2b covers what that required.

---

## 2. Problems found

### P1 — There was no installation, only a deployment 🔴

The fastest documented path was: fork the repo → create a Render account → provision a
Blueprint → wait for a Docker build → set two secrets in a web UI. Minutes of wall time
and a third-party account, before seeing a single screen.

*Why it is a problem:* the tool manages **your own** keys on **your own** machine. The
most natural first run — "let me try it locally" — was the least supported one.

### P2 — The default configuration could not boot 🔴

Verified, not theorised:

```
$ python -c "from app.main import create_asgi_app; create_asgi_app()"
ConfigurationError: PUBLIC_BASE_URL (or RENDER_EXTERNAL_URL) is required to run the
OAuth-secured MCP connector; set it or disable MCP_AUTH_ENABLED
```

And with a public URL present but no OAuth app — **exactly the state of a fresh Render
deploy after following README steps 1–3**:

```
ConfigurationError: GitHub OAuth is enabled but MCP_GITHUB_CLIENT_ID /
MCP_GITHUB_CLIENT_SECRET are not set
```

`MCP_ENABLED` and `MCP_AUTH_ENABLED` both defaulted to `true`, and
`build_auth_provider()` raised whenever credentials were missing. The README told users
to set only `FIRST_ADMIN_EMAIL`/`FIRST_ADMIN_PASSWORD`, so the advertised deployment
**crash-looped** until the user also created a GitHub OAuth App. An optional feature was
a hard startup dependency.

### P3 — The package had no entry point 🟠

`pyproject.toml` declared no `[project.scripts]`. After `pip install .` there was no
command — you had to know `uvicorn app.main:create_asgi_app --factory --host … --port …`.
Nothing to compare with `gh`, `uv` or `docker`.

### P4 — The wheel was structurally incapable of running 🔴

```toml
[tool.hatch.build.targets.wheel]
packages = ["app"]        # alembic/ and alembic.ini were outside app/
```

The migrations lived in a top-level `alembic/` directory, so they were **not in the
wheel**. The Docker image worked only because the Dockerfile `COPY`-ed them separately.
Any `pip install nim-key-manager` produced an installation that could never create its
own schema — the non-Docker path was broken by construction.

### P5 — Configuration was bound to the working directory 🔴 (security)

`SettingsConfigDict(env_file=".env")` reads `.env` relative to **`os.getcwd()`**. An
installed command run from anywhere else silently fell back to:

```python
jwt_secret            = "insecure-dev-secret-change-me"
encryption_master_key = "insecure-dev-master-key-change-me"
database_url          = "sqlite+aiosqlite:///./local.db"   # relative!
auto_create_tables    = True
```

Two consequences. First, a different `cd` meant a *different, empty* database — the
classic "where did my keys go?". Second, and much worse: real NVIDIA API keys would be
encrypted with a **master key published in this repository**, with no warning anywhere.

### P6 — No releases, no versions, no upgrade path 🟠

`git tag` was empty. No release workflow, no artifacts, no changelog. `app/__init__.py`
said `1.0.0` while `pyproject.toml` said `1.1.0`, so `/health` and `/docs` reported the
wrong version. There was no answer to "how do I update?" other than `git pull`.

### P7 — The container image was built and thrown away 🟠

CI ran `docker/build-push-action` with `push: false`. For a Docker-first project, users
could not `docker run` anything: they had to clone and build locally.

### P8 — Windows was undocumented and unsupported 🟠

`scripts/entrypoint.sh` is POSIX-only; every snippet assumed bash + `jq`; secret
generation assumed a shell. No PowerShell path existed. On Windows there was no
supported way to run this at all without Docker Desktop.

### P9 — Even the happy path was six manual steps 🟠

Clone → install Python 3.12 → `pip install -e ".[dev]"` (dev tooling, just to *run*) →
copy `.env.example` → run a script to generate secrets → remember the uvicorn factory
incantation. Every step is a place to fail, and none of it was scripted.

### P10 — Nothing to diagnose a broken install 🟡

No `doctor`, no `status`, no `update`, no `uninstall`. When something went wrong, the
user's only tool was a Python traceback.

---

## 2b. Problems found in the MCP experience

### M1 — The only transport was the hardest one 🔴

The MCP server existed, but exclusively as an OAuth-protected HTTP endpoint. To use it
from Claude Code on your own laptop you had to: deploy the service publicly (or run a
tunnel), register a GitHub OAuth App, copy two credentials, add yourself to an
allow-list, keep a web server running, and add a custom connector by URL.

For a **local** client talking to a **local** key store, every one of those steps is
ceremony. The MCP ecosystem's default transport is stdio precisely because the client can
just spawn the server — no port, no OAuth, no uptime. Not supporting it made the natural
setup the impossible one.

### M2 — "Install" ended before the useful part 🟠

Even with a perfect installer, the user was left to hand-edit a JSON file they had to
find first, with a command path they had to work out themselves. Registration is the
step that actually connects the tool to the thing that uses it.

### M3 — The implementation was visible everywhere 🟡

`uvicorn app.main:create_asgi_app --factory`, "Python toolchain (uv 0.9.x)", "creating an
isolated Python 3.12 runtime", `nimkm version` printing the interpreter path. A tool that
means to feel native to an ecosystem should not narrate its own build system.

### M4 — stdout was unusable for a protocol 🔴

`configure_logging()` wired structlog to **stdout**. Over stdio that is the JSON-RPC
channel: the first log line would have corrupted every session. Logging had to move to
stderr before anything could log.

### M5 — Two latent bugs the stdio path exposed 🟠

- `resolve_email()` fell back to `{subject}@mcp.local` for identities without an e-mail.
  `.local` is a special-use domain that e-mail validation rejects, so serialising that
  user through `UserOut` raised. Pre-existing, and the local identity would have hit it
  on every session.
- A fresh install has no users at all, so `resolve_actor` had nothing to act as. Verified
  by the first end-to-end run.

---

## 2c. What the pre-merge audit found in *this* work

Everything above was implemented and passing before this pass. Then it was
attacked deliberately, which is a different exercise from testing that it works.
Twelve defects surfaced; all are fixed, each with a regression test.

### Merge blockers

**A1 — A normal installation broke every tool call.** 🔴
`nimkm init` created the bootstrap administrator as `admin@localhost`. That
address is *rejected* by e-mail validation, and accounts created through the CLI
bypass the request schema — so it was accepted at creation and then failed when
serialised. The stdio session adopts the existing admin, so after
install → `mcp setup` → ask Claude anything, the answer was a validation error.

Found only by an end-to-end run that did `init` **before** the MCP session. The
existing e2e test started from an empty home, took the `local@nimkm.internal`
path, and passed. The default is now `admin@nimkm.internal`, any address that
cannot round-trip is refused up front with a clear message, and the e2e test is
parametrised over both entry orders.

**A2 — Registering rewrote the whole file.** 🔴
The writer emitted `json.dumps(..., indent=2)` regardless of the input, escaped
non-ASCII to `\uXXXX`, dropped a byte-order mark and (on Windows) converted LF
to CRLF. A user with a 4-space, minified, BOM-marked or non-English
configuration got a whole-file diff instead of a one-entry diff. Indentation
(spaces or tabs), newline style, BOM, trailing newline and Unicode are now
detected and reproduced.

**A3 — A concurrent write by the client was silently discarded.** 🔴
Claude Code writes to `~/.claude.json` while it runs. The read-modify-write had
no guard, so a change made in between was overwritten. The file is now stamped
(mtime + size) at read, checked again immediately before `os.replace`, and the
whole operation retried from the new content; a file that never settles gets an
explicit error instead of a lost update.

**A4 — Replacing the file widened its permissions.** 🔴
`os.replace` gives the destination the *temporary* file's mode, so a config the
user had restricted to `0600` came back as `0644` — on a file where these
clients keep their own credentials. The original mode is now applied to the
replacement before the swap.

### Serious

**A5 — Backups were unbounded and could clobber each other.** 🟠 Ten runs left
ten copies of a credential-bearing file in `$HOME`, and two changes within the
same second overwrote the earlier backup because the stamp had one-second
resolution. Now microsecond-stamped and pruned to the three most recent.

**A6 — A symlinked configuration was replaced by a regular file.** 🟠 Dotfile
managers (stow, chezmoi) symlink these paths. Writes now resolve the link and
replace the real file.

**A7 — A read-only or locked file produced a raw `WinError 5`.** 🟠 Now a
sentence that says what to do, and the temporary file is always cleaned up.

**A8 — One unreadable client aborted the whole command.** 🟠 `setup`, `remove`,
`status` and `doctor` now isolate failures per target and carry on.

**A9 — The registered command could be a bare name.** 🟠 If no launcher was
found on disk, `launcher()` returned `"nimkm"`, which the client would resolve
through `PATH` at spawn time — whatever happens to be first. It now returns an
absolute path or refuses to register.

**A10 — `nimkm update` trusted a URL from an API response.** 🟠 The
`browser_download_url` is now required to be HTTPS on a GitHub host before it is
handed to the installer; anything else falls back to PyPI.

### Quality

**A11 — Startup paid for Alembic on every spawn.** 🟡 An MCP client starts the
server on every session. Importing Alembic costs ~0.5 s and the database is
almost always already current, so the common case is now answered with one query
and Alembic is never imported. Measured: **2.3–2.9 s → ~2.0 s** per spawn.

**A12 — Two different commands were called `status`.** 🟡 `nimkm status` (the
dashboard) and `nimkm mcp status` (the registration) meant unrelated things.
`nimkm status` now shows the MCP picture first and the dashboard underneath.

### One test was flaky and would have failed CI at random

The raw JSON-RPC check asserted that the `tools/list` reply appeared on stdout.
When stdin closes immediately the process can exit before flushing it — a real
race, observed. The assertion (in the suite and in `installers.yml`) now targets
the handshake, which is deterministic, while still verifying that *every* line
of stdout parses as JSON-RPC.

---

## 3. What was implemented

### An MCP-first `nimkm` command (`app/cli.py`, `[project.scripts]`)

| Command | Purpose |
|---|---|
| **`nimkm mcp setup`** | detect MCP clients, confirm, back up, register (also `nimkm setup`) |
| **`nimkm mcp serve`** | speak MCP over **stdio** — what the client spawns |
| `nimkm mcp status` | where it is registered, under what name, pointing at which install |
| `nimkm mcp remove` | unregister, with the same backup guarantee |
| `nimkm mcp oauth` | the *remote* HTTPS connector (formerly `mcp setup`) |
| `nimkm web` (`serve`) | dashboard + REST API — optional, separate process |
| `nimkm doctor` | 9 checks with the fix for each, client registration first |
| `nimkm init` | generate secrets, write config, migrate, create the admin |
| `nimkm up` | initialise on first run and open the dashboard |
| `nimkm status` | is a dashboard answering `/health`? |
| `nimkm config path\|show\|set\|edit` | inspect/change settings (secrets masked) |
| `nimkm admin list\|create\|reset-password` | account recovery without SQL |
| `nimkm migrate` | apply migrations from anywhere |
| `nimkm update` | upgrade in place from the latest release |
| `nimkm uninstall [--purge]` | remove the install, optionally the data |

### The stdio transport (`app/mcp/stdio.py`)

Same 19 tools, same services, same RBAC and audit trail as the HTTP connector — different
trust model. Before any application import it forces the local profile through the
environment (`MCP_AUTH_ENABLED=false`, scheduler off, local identity), because
`Settings` is cached on first read and the tool layer reads it globally. Then it
redirects **all** logging to stderr, applies migrations, provisions an identity and
serves.

Two details that only matter in a child process: a fresh install self-heals (it writes
its configuration and migrates rather than failing the client's start-up), and the local
account is `local@nimkm.internal` — `.internal` is IANA-reserved for private use, so the
address can never resolve *and* it survives e-mail validation, which `.local`,
`.localhost` and `.invalid` do not.

### Client registration (`app/mcp/clients.py`)

Discovers Claude Code (user scope `~/.claude.json`, honouring `CLAUDE_CONFIG_DIR`;
project scope `./.mcp.json`) and Claude Desktop on all three platforms. Then, for each
target the user confirms:

1. parse the existing document — **refuse to touch it if it is not valid JSON**;
2. copy it to `<name>.bak-YYYYmmdd-HHMMSS`;
3. merge in one `mcpServers` entry, preserving every other key (Claude Code keeps
   unrelated state in that file);
4. write through a temporary file and `replace()`, so a crash cannot truncate it.

The entry uses the **absolute** launcher path plus an explicit `NIMKM_HOME`, because
clients are often started from a desktop session that never sourced a shell profile and
therefore has no useful `PATH`.

`--print` emits the JSON for any client this tool does not know, so nothing is a
dead end.

Standard library only, so a broken environment can still be *reported*; heavy imports
happen inside the commands that need them. Errors print one actionable line, with
`NIMKM_DEBUG=1` for the traceback.

### One home directory (`app/core/paths.py`)

`%LOCALAPPDATA%\nim-key-manager` · `~/Library/Application Support/nim-key-manager` ·
`${XDG_DATA_HOME:-~/.local/share}/nim-key-manager`, overridable with `NIMKM_HOME`,
containing `config.env` (mode 600), `data/nimkm.db`, `runtime/` and `bin/`. Backup is
"copy one folder"; uninstall is "delete one folder".

### Configuration that follows the install, not the shell (`app/core/config.py`)

`env_file=(config_file(), ".env")` with real environment variables still winning, an
**absolute** default SQLite URL inside the home directory, and `HOST`/`PORT` promoted to
settings. Same build, three environments, no surprises.

### Fail-closed secrets

`Settings.insecure_defaults()` + `require_secure_secrets()`, called from `create_app()`:
outside `development`/`test`, booting with the shipped placeholders now raises with the
command that fixes it. **P5 can no longer happen silently.**

### The connector degrades instead of crashing (`app/mcp/asgi.py`)

Unconfigured OAuth → log an actionable line and serve the API without `/mcp`.
Half-configured (credentials but no public URL) → still a hard error, because that is a
genuine mistake. **P2 fixed for both local and Render.**

### Migrations inside the package

`alembic/` → `app/migrations/`, plus `app/infrastructure/db/migrations.py` which drives
Alembic through its Python API with no `alembic.ini` and no checkout. The root
`alembic.ini` is kept for `alembic revision --autogenerate` during development. CI now
greps the built wheel to prove the revisions and the dashboard are inside it.

### Installers

`install.sh` (POSIX sh, ShellCheck-clean), `install.ps1` (Windows PowerShell 5.1
compatible — no `&&`, no ternaries, TLS 1.2 forced), `install.cmd` (double-click). Each
one: detects OS/arch (incl. musl and Apple Silicon) → downloads a **private** copy of
`uv` into the install home → creates an isolated Python 3.12 runtime → installs the
newest release wheel → writes a single-command shim → adds it to PATH → runs
`nimkm init` → prints the next command.

…then **offers to register the MCP server**, which is where the install actually becomes
useful. `curl | sh` leaves no stdin, so the POSIX installer reads the confirmation from
`/dev/tty` when there is one (and silently skips it in CI, where there is not);
`NIMKM_MCP_SETUP=1` accepts unattended, `NIMKM_NO_MCP_SETUP=1` declines.

Nothing global is touched: no system interpreter, no `sudo`, no package manager, and only
`nimkm` lands on PATH (not a whole scripts directory full of other executables). No
user-facing string in the installers or the CLI names Python, uv, uvicorn or FastAPI.

Artifact resolution degrades gracefully: pinned version → latest GitHub Release wheel →
PyPI → the `main` branch tarball. **The command in the README works today, before the
first release exists.**

### Zero-configuration container

`scripts/entrypoint.sh` provisions itself when the environment is empty: SQLite in
`/data`, secrets generated once into `/data/secrets.env` (mode 600), migrations via
`nimkm migrate`, then `nimkm serve`. Fully configured deployments behave exactly as
before. `docker run -p 8000:8000 -v nimkm:/data ghcr.io/bysergimm/nim-key-manager` is
now a complete instruction.

### Release automation (`.github/workflows/release.yml`)

`git tag v1.3.0 && git push --tags` →  version/tag consistency check → ruff + mypy +
pytest → wheel + sdist → `SHA256SUMS` → build provenance attestation → multi-arch
(amd64/arm64) GHCR image, also attested → GitHub Release with install instructions and
generated notes → optional PyPI publish via Trusted Publishing (no stored token).
Only first-party actions (`actions/*`, `docker/*`, `pypa/*`) are used.

### The promise is tested like a feature

- `installers.yml`: Ubuntu 22.04/latest, macOS 13/latest and Windows each install from
  scratch, register with a project-scoped client, run `doctor`, serve the dashboard,
  assert `/health` is `ok`, and uninstall cleanly. On Linux it also pipes a raw JSON-RPC
  session into the **installed launcher** and asserts every stdout line parses as JSON.
- `ci.yml`: also asserts the wheel contains the migrations *and* that a container with
  **no environment variables at all** boots healthy — the two defects that made
  installation impossible before.
- `tests/test_stdio.py`: a real MCP client drives `nimkm mcp serve` over a real pipe from
  an empty home — list tools, `whoami`, register a key, dispense it, check the stats.
- `tests/test_mcp_clients.py`: 30 tests on the config-editing path — unrelated state
  survives, other servers survive, corrupt files are refused, backups exist, writes are
  idempotent, no temporary file is left behind.
- `tests/test_cli.py`: 60+ tests including an end-to-end install into a throwaway home.

### Documentation

README rewritten around the first command (install → run → troubleshoot → FAQ →
update → uninstall), `README.es.md` rewritten to match, `docs/deployment.md` restructured
into local/container/Render, `docs/connector.md` given a `nimkm mcp setup` fast path,
`.env.example` and `CONTRIBUTING.md` updated for the new layout.

---

## 4. Verified on this machine

| Check | Result |
|---|---|
| `install.ps1` into a clean directory (no runtime present) | fetches its own interpreter, installs the wheel, writes shims |
| `nimkm init` | generates secrets, migrates, creates the admin, prints the password once |
| `nimkm doctor --offline` | 6 OK, 1 WARN (connector idle), exit 0 |
| `nimkm web --port 8137` | `/health` → `{"status":"ok","database":"up","version":"1.2.0"}`; `/` → 200 (13 KB dashboard); `/docs` → 200 |
| **MCP client over stdio, from an empty home** | 19 tools listed · `whoami` → `local@nimkm.internal` (admin) · `register_key` → active · `dispense_key` → returns the exact key · `stats_overview` → 1 |
| **Raw JSON-RPC piped into the launcher** | 2 responses, **every stdout line valid JSON**, `dispense_key` present |
| **`nimkm mcp setup` on a realistic config** | `numStartups`, `theme`, `projects` and a pre-existing `other-server` all preserved; backup written; re-run is a no-op; `mcp remove` leaves only `other-server` |
| Corrupt client config | refused with an explanation, file untouched |
| **The exact command written into the config** | spawned as a client would: 19 tools, `whoami` → `admin@nimkm.internal` (admin), `dispense_key` returns the right key |
| **`mcp setup` × 6 on a config with another server** | byte-identical file after the first run, 1 backup, `filesystem` server and `numStartups` untouched; `mcp remove` takes only our entry |
| **Adversarial probe suite** (29 checks) | formatting, Unicode, BOM, CRLF, tabs, minified, symlink, read-only, locked, JSONC, 1.7 MB config, races, backup pruning, launcher resolution — all pass |
| Startup latency, measured | cold 2.4 s · warm **2.0 s** (was 2.3–2.9 s) |
| Composed ASGI app with no MCP credentials | boots, logs `mcp_connector_not_configured` |
| `ruff check .` · `mypy app` · `pytest` | clean · clean · **261 passed, 3 skipped, coverage 90%** |

The Linux/macOS installer and the container are exercised by CI on their own runners.

---

## 5. Decisions, and what was rejected

### Runtime distribution

| Option | Verdict |
|---|---|
| **Private `uv` + managed CPython + release wheel** | **Chosen.** One artifact for every OS/arch, ~15 s install, no system Python, no `sudo`, trivially reproducible, and `uv` is the ecosystem's de-facto standard. |
| PyInstaller/Nuitka single-file binaries | Rejected. 6 build targets and ~80 MB each for an app with C extensions (`cryptography`, `asyncpg`) and dynamic imports (`fastmcp` providers); slow cold start; a permanent maintenance tax on a solo maintainer. The user-visible benefit over the chosen path is close to zero. |
| Assume a system Python + `pip install` | Rejected as the *primary* path. "Python 3.12+ and pip on PATH" is exactly the friction the brief asked to remove — and system Python on macOS/Debian is a minefield. Still offered as an option for people who already live there. |
| Docker-only | Rejected as primary. Requires Docker Desktop on Windows/macOS (licensing, multi-GB, VM) for a single-user tool. Kept as a first-class *secondary* path for servers. |
| Homebrew formula / Scoop / winget | Deferred, not rejected. All require either a published, stable release history or third-party review. They become worthwhile once releases exist — see §9. |

### Where the artifact comes from

GitHub Releases, resolved through the API, with PyPI and the `main` branch as fallbacks.
This satisfies "install the latest published artifact" while staying honest about the
fact that a pure-Python wheel is `py3-none-any` — one file for all platforms, so there is
no per-OS asset matrix to maintain.

### Do the installers auto-start the server?

**No, by default.** `curl … | sh` has no controlling stdin; a script that ends in a
foreground server is hostile in CI and confusing when Ctrl-C appears to "break the
install". Instead the installer finishes fully configured and prints exactly one next
command. `NIMKM_START=1` opts in.

### Single home directory vs XDG config/data/state split

Chosen: one directory (like `rustup`, `nvm`, `volta`). Correct XDG separation across
three OSes triples path handling and makes "back it up" or "remove it" a three-step
answer. Platform-appropriate *base* directories are still respected.

### Secrets: fail closed or warn?

Fail closed outside development. A key manager silently encrypting with a published key
is worse than a service that refuses to start with a one-line fix.

### stdio versus HTTP as the default transport

Chosen: **stdio by default, HTTP+OAuth for remote**. stdio is what the MCP ecosystem
assumes for local servers, and it removes an OAuth app, a public URL, a long-running
process and an open port from the setup path. The HTTP connector was kept unchanged for
the case it is actually good at — a client that is not on this machine.

Rejected: making stdio the *only* transport (it would break Claude on the web and the
existing Render deployment), and running a local HTTP server for local clients (a port,
a process to supervise, and either no auth or an OAuth dance for something the OS already
isolates).

### Does the local stdio session need its own authentication?

No, and adding one would be theatre. The client spawns the server as a child process
under the user's own account; the only way to reach it is that pipe. Anything able to
speak to it can already read `config.env` and the database directly. What *is* offered:
`--identity you@example.com` runs the session as a specific existing account, so a client
can be given a `viewer` role instead of admin. This is stated plainly in the README's FAQ
and in `docs/connector.md` rather than left implicit.

### Editing the user's client configuration

Registration always asks, never assumes, and always backs up first — including in the
installer, which reads the confirmation from `/dev/tty` rather than skipping consent. A
file that does not parse as JSON is never rewritten: the tool reports it and stops. The
alternative (shelling out to `claude mcp add`) was rejected because it would hand the
backup guarantee to another program and only work for one client.

### Is `git mv alembic app/migrations` too invasive?

It is the only way to make `pip install` produce a usable installation (P4), it is
invisible to users, and the root `alembic.ini` keeps the development workflow identical.
Worth it.

---

## 6. Installing, per operating system

| OS | Command |
|---|---|
| **Linux** | `curl -fsSL https://raw.githubusercontent.com/BySergiMM/nim-key-manager/main/install.sh \| sh` |
| **macOS** (Intel & Apple Silicon) | same command |
| **Windows 10/11** | `irm https://raw.githubusercontent.com/BySergiMM/nim-key-manager/main/install.ps1 \| iex` |
| **Windows (cmd / double-click)** | `install.cmd` |
| **Any OS with Docker** | `docker run -d -p 8000:8000 -v nimkm:/data ghcr.io/bysergimm/nim-key-manager` |
| **Existing Python tooling** | `uv tool install nim-key-manager` · `pipx install nim-key-manager` |
| **Hosted, public URL** | Render Blueprint button |

Then confirm the registration prompt (or run `nimkm mcp setup`) and restart your MCP
client. Overrides: `NIMKM_VERSION`, `NIMKM_HOME`, `NIMKM_BIN_DIR`, `NIMKM_NO_MODIFY_PATH`,
`NIMKM_NO_INIT`, `NIMKM_MCP_SETUP`, `NIMKM_NO_MCP_SETUP`.

Step count for the target user: **two** — the install command, and answering `Y`.

---

## 7. Publishing a new version

```bash
# 1. bump the single source of truth
$EDITOR app/__init__.py          # __version__ = "1.3.0"

# 2. commit and tag
git commit -am "chore: release 1.3.0"
git tag v1.3.0
git push origin main --tags
```

That is the entire release process. If the tag and `__version__` disagree, the workflow
stops before publishing anything.

Users upgrade with `nimkm update` (or `docker pull`). New installs pick up the new wheel
automatically, because the installers ask the Releases API for the latest one.

---

## 8. How the pipeline works

```
 push / PR ──▶ ci.yml
                ├── ruff · mypy · pytest (coverage ≥85%)
                ├── build wheel ▸ assert migrations + dashboard are inside it
                └── build image ▸ run it with NO env ▸ assert /health is ok

 installer or CLI change ──▶ installers.yml
                ├── shellcheck install.sh
                ├── ubuntu-22.04 · ubuntu-latest · macos-13 · macos-latest
                │      install ▸ doctor ▸ serve ▸ /health ▸ uninstall --purge
                └── windows-latest: same, via install.ps1

 git push --tags ──▶ release.yml
                ├── verify    tag == app.__version__, full quality gate
                ├── build     wheel + sdist + SHA256SUMS + provenance attestation
                ├── container ghcr.io/bysergimm/nim-key-manager:{version,latest}
                │              linux/amd64 + linux/arm64, attested, pushed
                ├── release   gh release create, assets attached, notes generated
                └── pypi      optional, Trusted Publishing (vars.PUBLISH_TO_PYPI)

 green CI on main ──▶ deploy.yml ──▶ Render deploy hook
```

The installers read the **Release**, so nothing reaches users until every previous stage
is green.

---

## 9. If this had millions of users

Ordered by what I would actually do first.

0. **Get into the MCP registries.** Now that the server is a first-class MCP citizen, the
   distribution channel that matters most is not GitHub Releases — it is the client's own
   "add a server" list. Publish to the MCP registry with a `server.json`, and keep the
   `nimkm mcp setup` path for everyone else. That turns two steps into one.

1. **Own the install URL.** `curl -fsSL https://nimkm.dev/install | sh` instead of a
   `raw.githubusercontent.com` path. It survives repo renames, lets you fix a broken
   installer without a new release, and it is what every tool in this category does.
   Serve the script from a CDN with a short TTL and log versions/platforms/failures — a
   quiet, aggregate install funnel is the single most useful telemetry a distributor can
   have.
2. **Sign everything, and verify it in the installer.** Today: SHA256SUMS plus GitHub
   build-provenance attestations. At scale: Sigstore/cosign signatures, and the
   installer verifying the signature *before* executing anything. A `curl | sh` endpoint
   is a supply-chain crown jewel.
3. **Get into the package managers people already use.** Homebrew tap/core, Scoop and
   winget manifests, an AUR package, a `.deb`/`.rpm` via `nfpm`, and a Helm chart for the
   Kubernetes crowd. Generate all manifests from one release job so they cannot drift.
4. **Ship a real update channel.** Signed version manifest, `nimkm update --channel
   stable|beta`, staged rollouts (10 % → 50 % → 100 %), automatic rollback on a spike in
   post-update `doctor` failures, and a background "update available" notice with an
   opt-out.
5. **Make PostgreSQL the default for multi-user installs, and keep SQLite honest.**
   SQLite is right for one person; add WAL mode, a `nimkm backup`/`nimkm restore` pair,
   and a `nimkm db migrate-to-postgres` command so growth never means data loss.
6. **Treat the encryption key as the product's hardest problem.** Today it is a random
   string in `config.env`. At scale: envelope encryption with per-key DEKs, a pluggable
   KMS backend (AWS KMS, GCP KMS, Vault, `age` for self-hosters), key rotation without
   re-encrypting the world, and OS keychain integration for local installs. Also: a
   loud, unmissable "back up this file" moment during `init`, because the current design
   has exactly one point of unrecoverable failure.
7. **Split the deployable unit.** One process today runs the API, the dashboard, the MCP
   server and the APScheduler jobs. At scale the scheduler must become a leader-elected
   or externally-triggered worker, otherwise N replicas run N validation sweeps against
   NVIDIA and rate-limit each other.
8. **Replace in-memory OAuth storage.** Without `MCP_REDIS_URL`, Claude re-authorises
   after every restart. That is fine for one person and unacceptable at scale — make
   persistent storage the default, backed by the primary database rather than an extra
   service.
9. **Instrument the first five minutes.** Opt-in, anonymous, aggregate: install success
   rate by platform, time-to-first-key, `doctor` check failure distribution. Every DX
   claim in this document is currently an assertion; at scale it should be a number.
10. **A tiny hosted tier.** Most people who want this do not want to run a service. The
    same image behind a managed offering (per-tenant encryption keys, SSO) is the natural
    business model — and it keeps the self-hosted path honest, because it is the same
    artifact.

Two more that are cheap now and get expensive later: a real `CHANGELOG.md` with
`release-please`, and Dependabot on Actions + pip.

---

## 9b. What a maintainer should still watch

Ranked by what would actually bite first.

1. **`app/cli.py` is ~1,100 lines.** It is cohesive and sectioned, but it is the
   file most likely to rot. The natural split is a `cli/` package with one
   module per command group; worth doing before the next feature lands in it,
   not as part of this change.
2. **Client locations are a fixed list.** `~/.claude.json`, `./.mcp.json` and
   the Claude Desktop path are read from the code, not discovered. If a client
   moves its configuration, the failure is graceful (nothing detected → print
   the snippet → no file written), but the user has to notice. `claude mcp add`
   stays the vendor-supported escape hatch and is documented as such.
3. **~2.0 s to start a session** is dominated by importing the MCP SDK (~0.8 s)
   and SQLAlchemy (~0.6 s). Both are needed to serve a single tool call, so the
   remaining wins are in the dependencies, not here.
4. **The stdio session runs as an administrator by default.** Correct for the
   trust model, but `--identity` (to run as a `viewer`) is opt-in and easy to
   miss on a shared machine.
5. **The `main`-branch fallback in the installers** means a user can install
   unreleased code before the first tag exists. Remove it once v1.2.0 ships.

## 10. Known limitations

- **The `main`-branch fallback in the installers is a bootstrap convenience.** Once the
  first release is tagged, resolution always finds the wheel; consider removing the
  fallback afterwards so users never silently install unreleased code.
- **`nimkm uninstall` on Windows cannot delete the interpreter it is running from.** It
  removes what it can, reports the rest and exits 0, rather than pretending.
- **Coverage moved from 94 % to 90 %.** The CLI added ~600 statements; the parts that
  spawn a server are covered end-to-end by CI rather than by unit tests.
- **`CORS_ORIGINS` still defaults to `["*"]` with credentials enabled.** Out of scope for
  this work, but it should be tightened to `PUBLIC_BASE_URL` by default.
- **The Render Blueprint still asks for `FIRST_ADMIN_*`.** That is fine, but it means the
  hosted path and the local path create the first admin differently; unifying them behind
  `nimkm admin create` would remove one concept.
- **Client discovery is a fixed list.** Claude Code and Claude Desktop are detected by
  their known configuration paths. Other MCP clients get `--print`. A registry of client
  layouts (or reading each client's own CLI) would generalise this, but every entry is a
  file format that can change under us — the current code fails safe when it does.
- **`nimkm mcp serve` grants the local session admin rights by default.** That matches
  the trust model (whoever spawns it already owns the data), but a `viewer`-by-default
  option with elevation on demand would be a better fit for shared machines.
- **A stale registration is detected, not repaired.** `nimkm mcp status` and `doctor`
  warn when the registered command points at a different install; `nimkm update` could
  re-point it automatically.

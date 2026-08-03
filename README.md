<div align="center">

# NIM Key Manager

<img width="1200" height="630" alt="NIM Key Manager" src="https://github.com/user-attachments/assets/1dc02b55-1a6d-41ff-a76b-14abb53a853d" />

**An MCP server for the API keys of _your own_ NVIDIA Build/NIM account.**

Ask Claude for a key and it hands you one — encrypted at rest, rotated on request, every access audited.

[![CI](https://github.com/BySergiMM/nim-key-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/BySergiMM/nim-key-manager/actions/workflows/ci.yml)
[![Installers](https://github.com/BySergiMM/nim-key-manager/actions/workflows/installers.yml/badge.svg)](https://github.com/BySergiMM/nim-key-manager/actions/workflows/installers.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%2B%20OAuth-8A2BE2.svg)](https://modelcontextprotocol.io)

</div>

## Install

**Linux / macOS**

```bash
curl -fsSL https://raw.githubusercontent.com/BySergiMM/nim-key-manager/main/install.sh | sh
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/BySergiMM/nim-key-manager/main/install.ps1 | iex
```

The installer finds Claude Code on your machine and asks for permission to register the
server (it backs the file up first). Say yes, restart Claude Code, and you are done.

Already installed, or answered no?

```bash
nimkm mcp setup
```

That is everything. There is no runtime to install, no database to provision, no config
file to write, no API server to keep running.

## Then just ask

> **"list my NVIDIA keys"**
> **"give me a NIM key for this script"**
> **"register this key I just created at build.nvidia.com"**
> **"rotate the prod-1 key, here is the new one"**
> **"which of my keys expire this month?"**
> **"who used my keys last week?"**

Claude picks the right tool, the server enforces roles and writes the audit entry.

<details>
<summary><b>The 19 tools it exposes</b></summary>

| Area | Tools |
|---|---|
| Keys | `list_keys` · `get_key` · `register_key` · `validate_key` · `rotate_key` · `revoke_key` · `delete_key` · `check_expirations` |
| Use | `dispense_key` (least-recently-used active key, globally or per project) |
| Projects | `list_projects` · `get_project` · `create_project` · `update_project` · `delete_project` · `assign_key_to_project` |
| Insight | `stats_overview` · `usage_stats` · `list_audit` |
| Session | `whoami` |

`dispense_key` is the one that returns plaintext — everything else works with hints and
fingerprints, and nothing is ever written to a log.

</details>

## Other MCP clients

`nimkm mcp setup` knows Claude Code (user and project scope) and Claude Desktop. For
anything else, print the declaration and paste it in:

```bash
nimkm mcp setup --print
```

```json
{
  "mcpServers": {
    "nimkm": {
      "command": "/home/you/.local/share/nim-key-manager/bin/nimkm",
      "args": ["mcp", "serve"],
      "env": { "NIMKM_HOME": "/home/you/.local/share/nim-key-manager" }
    }
  }
}
```

| Flag | Effect |
|---|---|
| `--scope user` | Claude Code, all your projects (`~/.claude.json`) |
| `--scope project` | this repository only (`./.mcp.json`, commit it for your team) |
| `--scope desktop` | Claude Desktop |
| `--name <name>` | register under a different name |
| `--yes` | skip the confirmation prompt |
| `--print` | print the JSON, change nothing |

`nimkm mcp status` shows where it is registered; `nimkm mcp remove` undoes it. Every
write is preceded by a timestamped backup of the file.

## The dashboard (optional)

The same instance has a web UI and a REST API for the things a chat is bad at — browsing
the audit trail, managing accounts, wiring an application to `/api/v1/keys/dispense`.

```bash
nimkm web            # http://127.0.0.1:8000
```

It is off unless you start it. The MCP server does not need it.

<details>
<summary><b>REST quickstart</b></summary>

```bash
BASE=http://127.0.0.1:8000

TOKEN=$(curl -s $BASE/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@nimkm.internal","password":"<the password nimkm printed>"}' | jq -r .access_token)

curl -s "$BASE/api/v1/keys/dispense" -H "Authorization: Bearer $TOKEN"
```

More in [`docs/api-examples.md`](docs/api-examples.md); interactive OpenAPI at `/docs`.
Lost the password? `nimkm admin reset-password --email admin@nimkm.internal`.

</details>

## Commands

```bash
nimkm mcp setup           # connect to your MCP client (the only one you need)
nimkm mcp status          # where it is registered, and as what
nimkm mcp serve           # the server itself -- your client runs this, not you
nimkm mcp remove          # unregister
nimkm doctor              # diagnose everything, with the fix for each problem
nimkm web                 # dashboard + REST API
nimkm admin reset-password --email you@example.com
nimkm update              # upgrade in place
nimkm uninstall --purge   # remove everything
```

## Remote access (advanced)

Everything above is local: your client spawns the server as a child process and no port
is opened. If you want Claude on the web or on your phone to reach the *same* instance,
deploy it and use the OAuth-protected HTTP connector instead:

```bash
docker run -d -p 8000:8000 -v nimkm:/data ghcr.io/bysergimm/nim-key-manager
nimkm mcp oauth      # GitHub/Google OAuth for the /mcp endpoint
```

Or one click on Render for a managed PostgreSQL and an HTTPS URL:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/BySergiMM/nim-key-manager)

Then in Claude: **Settings → Connectors → Add custom connector** → `‹your-url›/mcp`.
Only identities in `MCP_ALLOWED_IDENTITIES` may connect (fail-closed). Full guide:
[`docs/connector.md`](docs/connector.md) · deployment options: [`docs/deployment.md`](docs/deployment.md).

> **NVIDIA Terms of Service.** NVIDIA Build offers **no public API** to create or rotate
> keys (you generate them in the portal), and API keys **must not be shared or
> redistributed**. This project manages **your own keys on your own machine**: the only
> outbound call is the official read-only validation endpoint
> `GET https://integrate.api.nvidia.com/v1/models`. It does not automate or scrape the
> NVIDIA portal, and it is **not** a service for handing your keys to other people.

## Where things live

One directory, easy to back up and easy to delete:

| | Path |
|---|---|
| **Linux** | `~/.local/share/nim-key-manager` |
| **macOS** | `~/Library/Application Support/nim-key-manager` |
| **Windows** | `%LOCALAPPDATA%\nim-key-manager` |

```
<home>/config.env      generated secrets, chmod 600
<home>/data/nimkm.db   your encrypted keys
<home>/runtime/        the self-contained runtime
<home>/bin/nimkm       the launcher your MCP client runs
```

Override with `NIMKM_HOME`. Moving to a new machine means copying that folder — the data
is useless without `ENCRYPTION_MASTER_KEY`, so copy both or neither.

## Update & uninstall

```bash
nimkm update                # in place; keeps config, data and your registrations
nimkm uninstall             # remove the program, keep the keys
nimkm uninstall --purge     # remove everything
```

`nimkm mcp remove` first if you want the entry gone from your client config.

## Troubleshooting

`nimkm doctor` checks the client registration, secrets, database, migrations, ports and
NVIDIA reachability, and prints the command that fixes each problem. Start there.

| Symptom | Cause / fix |
|---|---|
| Claude does not list the tools | Restart the client — MCP servers are read at startup. Then `nimkm mcp status`. |
| `nimkm: command not found` | The PATH change applies to *new* shells. Open a new terminal, or `source ~/.profile`. |
| `irm … \| iex` fails on Windows | `Set-ExecutionPolicy -Scope Process Bypass` first, or download `install.ps1` and run it with `-ExecutionPolicy Bypass`. |
| Client shows the server as failed | Run `nimkm mcp serve` yourself: it should sit there silently waiting for input. Any error appears on stderr. |
| Registered, but the wrong install answers | `nimkm mcp status` warns when the entry points elsewhere; `nimkm mcp setup` repoints it. |
| "not valid JSON … refusing to overwrite" | Your client's config file is corrupt. Fix or move it; the tool will never rewrite a file it cannot parse. |
| `port 8000 is already in use` | Only affects `nimkm web`: `nimkm web --port 8001`. |
| Dashboard login fails | `nimkm admin reset-password --email you@example.com`. |
| Container forgot everything | You ran it without a volume: `-v nimkm:/data`. |

`NIMKM_DEBUG=1 nimkm <command>` prints the full traceback.
[Issues](https://github.com/BySergiMM/nim-key-manager/issues) welcome.

## FAQ

**What do I need installed?**
Nothing. The installer brings a self-contained runtime; there is no interpreter, service
or database to set up.

**Does it run all the time?**
No. Your MCP client starts it when it needs it and stops it when it closes. The dashboard
is a separate, optional process.

**Is it safe over stdio without a login?**
The transport *is* the boundary: the server runs as a child of your own client, with your
own file permissions, reachable only through that pipe. Anything that could speak to it
could already read your config file. Remote access is the case that needs OAuth, and it
has it.

**Where do my keys go?**
Into a local database, encrypted with AES-256-GCM using a secret generated on your
machine at install time. They never leave it except when you ask for one.

**Can I use it from more than one client?**
Yes — register it in each one; they share the same database. `--scope project` puts the
declaration in `./.mcp.json` so a team gets it from version control (they each keep their
own keys).

**Which database?**
SQLite by default — no setup. For a shared server:
`nimkm init --database-url postgresql+asyncpg://user:pass@host/db`.

**Can I run it without an AI client at all?**
Yes: `nimkm web` gives you the dashboard and the REST API.

## Under the hood

```
app/
├── mcp/              # the MCP surface: stdio transport, client registration,
│                     #   tools, OAuth for remote access, identity mapping
├── domain/           # enums and domain exceptions (no dependencies)
├── application/      # use cases (services) and ports (interfaces)
├── infrastructure/   # SQLAlchemy repositories, NVIDIA gateway, migrations
├── api/              # REST layer for the dashboard and integrations
├── dashboard/        # the web UI
├── migrations/       # Alembic revisions, shipped inside the package
├── core/             # config, paths, crypto, security, logging
└── cli.py            # the nimkm command
```

Pragmatic Clean Architecture: dependencies point inward, and the MCP tools and the REST
endpoints are two adapters over the *same* services — so roles and the audit trail behave
identically whichever way a key is touched. Details in
[`docs/architecture.md`](docs/architecture.md); the installation and release design in
[`INSTALLATION_REDESIGN.md`](INSTALLATION_REDESIGN.md).

## Development

```bash
git clone https://github.com/BySergiMM/nim-key-manager.git
cd nim-key-manager
pip install -e ".[dev]"

ruff check . && mypy app && pytest --cov=app   # the CI gate (coverage ≥85%)
```

Point your own client at the checkout with `nimkm mcp setup --scope project`. Releases:
bump `__version__` in `app/__init__.py`, then `git tag v1.3.0 && git push --tags`.

## Security

[`docs/security.md`](docs/security.md) has the threat model: AES-256-GCM with an
HKDF-derived key, SHA-256 fingerprints for deduplication without exposing secrets,
argon2 password hashing, short-lived JWTs for the web layer, rate limiting, and an
immutable audit trail. The service refuses to start in production with the shipped
development placeholders. Vulnerabilities: [`SECURITY.md`](SECURITY.md).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md).
Spanish overview: [`README.es.md`](README.es.md). MIT — see [LICENSE](LICENSE).

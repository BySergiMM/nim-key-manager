# NIM Key Manager
<img width="1200" height="630" alt="NIM-KE~1" src="https://github.com/user-attachments/assets/1dc02b55-1a6d-41ff-a76b-14abb53a853d" />

**Self-hosted, production-ready manager for the API keys of _your own_ NVIDIA Build/NIM account** — encrypted storage, assisted rotation, expiry detection, usage stats, projects, RBAC, audit, a web dashboard, and a **Claude MCP connector**. Deploy your own instance in a few minutes; everything is configured through environment variables.

[![CI](https://github.com/BySergiMM/nim-key-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/BySergiMM/nim-key-manager/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Coverage](https://img.shields.io/badge/coverage-94%25-brightgreen.svg)](#development--tests)

> **NVIDIA Terms of Service.** NVIDIA Build offers **no public API** to create or rotate keys programmatically (you generate them at [build.nvidia.com](https://build.nvidia.com/settings/api-keys)), and API keys **must not be shared or redistributed** to third parties. This project is therefore designed for **you to manage your _own_ keys on your _own_ instance**: the only outbound call is the official read-only **validation** endpoint `GET https://integrate.api.nvidia.com/v1/models`. It does not automate or scrape the NVIDIA portal, and it is **not** a service for handing your keys to other people.

## Deploy your own (no local setup)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/BySergiMM/nim-key-manager)

1. Click the button (or **Use this template → Create repository**, then in [Render](https://render.com) pick **New → Blueprint** and select your fork). Render reads [`render.yaml`](render.yaml) and provisions **everything automatically**:
   - a managed PostgreSQL database,
   - the Dockerized web service with a health check,
   - the secrets `JWT_SECRET`, `ENCRYPTION_MASTER_KEY` and `MCP_OAUTH_JWT_SIGNING_KEY` (generated and stored by Render's secret manager),
   - `DATABASE_URL` injected from the database.
2. When prompted, set `FIRST_ADMIN_EMAIL` and `FIRST_ADMIN_PASSWORD` (your initial admin, created on first boot).
3. Open the service URL: log in at `/`, explore the API at `/docs`.
4. Every push to `main` runs CI (lint + types + tests + build) and redeploys automatically. Migrations (`alembic upgrade head`) run on container start.

Prefer another host? Any platform that runs a Docker container + PostgreSQL works — see [`docs/deployment.md`](docs/deployment.md).

## Use it from Claude (MCP connector)

The same deployment exposes a **Model Context Protocol server** at `‹BASE›/mcp` so you can add it to **Claude as a custom connector**. Claude authenticates with **OAuth 2.1** (GitHub by default, Google optional) and can list/inspect keys, **dispense** a ready-to-use key, register/rotate/revoke and manage projects — with the same RBAC and audit trail as the REST API. Only identities in `MCP_ALLOWED_IDENTITIES` may connect (fail-closed).

1. Create a GitHub **OAuth App** with callback `‹BASE›/auth/callback`; copy the Client ID/Secret.
2. In Render set `MCP_GITHUB_CLIENT_ID`, `MCP_GITHUB_CLIENT_SECRET` and `MCP_ALLOWED_IDENTITIES` (your GitHub login/e-mail). The rest is already in `render.yaml`.
3. In Claude: **Settings → Connectors → Add custom connector** → URL `‹BASE›/mcp` → **Connect**.

Full guide (Google, tool reference, security, troubleshooting): [`docs/connector.md`](docs/connector.md).

## Features

- **Secure key registration** — encrypted at rest with AES-256-GCM (key derived via HKDF-SHA256 from the platform secret manager). Never stored or logged in plaintext.
- **Assisted, audited rotation** — you create the new key in your NVIDIA account, paste it, and the system performs an atomic swap (new key active, old one revoked with a `rotated_from_id` lineage link).
- **Expiry detection** — hourly background job + maintenance endpoint; configurable `expiring_soon` flag.
- **Periodic validation** — automatic sweep against NVIDIA every 6 h (configurable) that flags invalid/revoked keys.
- **Key dispensing** — `GET /api/v1/keys/dispense` returns the least-recently-used active key (LRU), globally or per project, recording usage.
- **Projects** — group keys by consumer/workload.
- **Statistics** — inventory by status, dispenses, usage time series.
- **Security** — JWT (access + refresh), roles `admin`/`manager`/`viewer`, rate limiting, immutable audit of every sensitive operation.
- **Claude connector (MCP)** — OAuth-secured MCP server at `/mcp` (see above).
- **Operations** — structured JSON logging, Prometheus metrics at `/metrics`, health check at `/health`, OpenAPI at `/docs`.

## Architecture

```
app/
├── domain/           # Enums and domain exceptions (no dependencies)
├── application/      # Use cases (services) and ports (interfaces)
│   └── services/     # auth, users, keys, projects, stats, audit
├── infrastructure/   # Adapters: SQLAlchemy (repositories) and NVIDIA gateway
├── api/              # FastAPI: routers, schemas, deps, rate limiting
├── mcp/              # Claude connector: MCP server, OAuth and identity mapping
├── dashboard/        # Lightweight SPA served at /
├── tasks/            # Scheduled jobs (APScheduler)
└── core/             # Config, crypto, security, logging
```

Pragmatic Clean Architecture: dependencies point inward; the application layer knows nothing about FastAPI and reaches NVIDIA through the `KeyValidator` port. Details and decisions in [`docs/architecture.md`](docs/architecture.md).

**Stack**: Python 3.12 · FastAPI · FastMCP (Claude connector) · SQLAlchemy 2 (async) · managed PostgreSQL · Alembic · Docker · GitHub Actions · Render (Blueprint) · structlog · Prometheus · slowapi · APScheduler.

## API quickstart

```bash
BASE=https://your-service.onrender.com

# Log in (the admin was created on first boot)
TOKEN=$(curl -s $BASE/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"your-password"}' | jq -r .access_token)

# Register a key created at build.nvidia.com (validating it against NVIDIA)
curl -s $BASE/api/v1/keys -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"prod-1","api_key":"nvapi-...","validate_remote":true}'

# Get an available key (LRU) to use in your application
curl -s "$BASE/api/v1/keys/dispense" -H "Authorization: Bearer $TOKEN"
```

More examples (rotation, projects, stats, audit) in [`docs/api-examples.md`](docs/api-examples.md). Interactive OpenAPI at `/docs`.

## Roles

| Operation | viewer | manager | admin |
|---|---|---|---|
| View keys, projects and stats | ✅ | ✅ | ✅ |
| Register / validate / rotate / revoke / dispense keys | ❌ | ✅ | ✅ |
| Manage projects | ❌ | ✅ | ✅ |
| Delete keys, manage users, read the audit log | ❌ | ❌ | ✅ |

## Development & tests

Not required to deploy, but fully supported:

```bash
pip install -e ".[dev]"
pytest --cov=app          # required coverage gate: 85% (currently ~94%)
ruff check . && mypy app
docker compose up         # local stack with PostgreSQL
```

## Security

Threat model, cryptographic details and design decisions in [`docs/security.md`](docs/security.md). Key points: AES-256-GCM encryption with a key derived (HKDF) from the secret manager, SHA-256 fingerprints to deduplicate without exposing the secret, short-lived signed JWTs, argon2 password hashing, per-IP rate limiting, audit of every sensitive operation, and no plaintext keys in logs or responses except the explicit dispense endpoint. To report a vulnerability, see [`SECURITY.md`](SECURITY.md).

## Contributing

Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). A short Spanish overview is available in [`README.es.md`](README.es.md).

## License

MIT — see [LICENSE](LICENSE).

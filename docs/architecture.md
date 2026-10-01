# Architecture

## Overview

NIM Key Manager follows **pragmatic Clean Architecture** with four layers and dependencies pointing inward:

```
        ┌──────────────────────────────────────────────┐
        │ api/ (FastAPI) · mcp/ · dashboard/ · tasks/  │  ← Interface / delivery
        ├──────────────────────────────────────────────┤
        │ application/services + interfaces            │  ← Use cases and ports
        ├──────────────────────────────────────────────┤
        │ domain/ (enums, exceptions)                  │  ← Rules and vocabulary
        ├──────────────────────────────────────────────┤
        │ infrastructure/ (SQLAlchemy, NVIDIA)         │  ← Adapters
        └──────────────────────────────────────────────┘
```

- **domain**: business vocabulary (roles, key statuses, audit actions) and exceptions. No external dependencies.
- **application**: the use cases (`KeyService`, `AuthService`, `ProjectService`, `UserService`, `StatsService`, `AuditService`). They reach persistence through repositories and NVIDIA through the `KeyValidator` port (a `Protocol`), which makes it trivial to swap in a fake in tests.
- **infrastructure**: concrete adapters — async SQLAlchemy 2 repositories and `NvidiaKeyValidator` (httpx).
- **api** and **mcp**: two inbound adapters that translate HTTP/MCP ⇄ use cases. Domain exceptions are mapped to HTTP status codes in a single place (`main.py`), so routers contain no error handling. The MCP layer reuses the exact same services, so RBAC and audit behave identically across REST and the Claude connector.

### Notable decisions

1. **Entities = ORM models.** Instead of duplicating every entity as dataclass + model + mappers, services operate on the SQLAlchemy models and routers convert them to Pydantic schemas. The dependency direction is preserved (the API never queries the DB directly; services never import FastAPI) with half the code. This is the standard trade-off for services of this size.
2. **Assisted rotation, not automated.** NVIDIA Build exposes no official key creation/rotation API; automating the portal would violate the ToS. `POST /keys/{id}/rotate` performs the atomic, audited swap once the operator pastes the new key. The `KeyValidator` port leaves the extension point ready should NVIDIA publish an official management API.
3. **LRU dispensing.** `GET /keys/dispense` picks the least-recently-used, non-expired active key (`last_used_at NULLS FIRST, usage_count`), so usage is spread evenly over the keys of your account and each key's `last_used_at` / `usage_count` stay meaningful for audit and expiry. It is only an ordering: it does not raise, bypass or enforce any NVIDIA rate limit, which is whatever NVIDIA applies to your account, and rotating among keys must not be used to get around it (see "NVIDIA service compliance" in [`security.md`](security.md)).
4. **SHA-256 fingerprint** of each key to detect duplicates without decrypting the inventory.
5. **Alembic migrations as the source of truth** in production (`AUTO_CREATE_TABLES=false`); `create_all` is only used in tests/development.
6. **Factory pattern** (`create_app()` / `create_asgi_app()`): the app is built per process (uvicorn `--factory`), which eases isolated tests and avoids global state. `create_asgi_app()` composes the REST/dashboard app with the MCP connector.

## Data model

```
users 1─────* api_keys *─────1 projects
                 │  └── rotated_from_id (rotation lineage, self-FK)
                 └────* usage_records
audit_logs (append-only, no FKs so it survives deletions)
```

## Main flows

**Key registration**: validate `nvapi-` format → (optional) remote validation against NVIDIA → AES-GCM encryption → fingerprint → audit → commit.

**Dispensing**: LRU selection → decryption → usage update → `usage_record` → audit. The only API response that contains the plaintext key.

**Expiry**: an hourly job (APScheduler) + a maintenance endpoint mark overdue keys `expired`; `expiring_soon` warns `EXPIRY_WARNING_DAYS` in advance.

**Validation sweep**: every `VALIDATION_INTERVAL_HOURS`, each active key is validated against NVIDIA; rejected ones become `invalid` (and recover to `active` if they validate again).

## Claude connector (MCP)

The `mcp/` package is an inbound adapter that exposes the use cases as Model Context Protocol tools over an OAuth 2.1-secured HTTP endpoint, mounted at `/mcp` on the same service. The OAuth identity presented by Claude is mapped to an application user, so every tool call runs through the same role checks and audit trail as the REST API. See [`connector.md`](connector.md).

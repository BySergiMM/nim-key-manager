# Security

## Encryption at rest

- Every API key is encrypted with **AES-256-GCM** (random 96-bit nonce per operation, authenticated).
- The data key is derived from the master secret `ENCRYPTION_MASTER_KEY` via **HKDF-SHA256** (fixed, versioned salt and info). The master secret is generated and held by the platform's secret manager (Render `generateValue`); it never appears in the repository.
- Storage format: `enc$v1$<nonce_b64>$<ciphertext_b64>` — the version prefix allows rotating the crypto scheme with incremental re-encryption.
- **SHA-256 fingerprint** of the key for deduplication and identification without decryption.
- **Hint** (`…XXXX`, last 4 characters) as the only value shown in the UI/logs.

## Authentication and authorization

- Passwords hashed with **argon2id** (pwdlib "recommended").
- Signed **JWTs** (HS256, secret from the secret manager) with an explicit `type`: access (30 min) and refresh (7 days). A refresh token cannot be used as an access token or vice versa.
- Hierarchical **RBAC**: `viewer < manager < admin`. The full matrix is in the README. Invariant guards: the last active admin cannot be removed/demoted.
- Safe bootstrap: the first administrator is created at start-up from `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD` (or with `scripts/create_admin.py` on a host with database access). No HTTP request can create it: `POST /api/v1/auth/register` is admin-only from the very first request, so a fresh deployment cannot be taken over by whoever reaches it first. See [`deployment.md`](deployment.md#first-administrator).

## Claude connector (MCP) security

- The `/mcp` endpoint is protected by **OAuth 2.1** (Authorization Code + PKCE) via GitHub or Google. Claude cannot paste a bearer token — it must complete the OAuth flow.
- An **allow-list** (`MCP_ALLOWED_IDENTITIES`) gates who may connect; it is **fail-closed** (empty = deny everyone). It holds stable account ids (GitHub's numeric user id, Google's `sub`) and e-mail addresses the provider vouches for; GitHub logins are not accepted because a login can be renamed and then registered by somebody else. See [`connector.md`](connector.md#migrating-an-existing-allow-list).
- The OAuth identity maps to an application user, so the same RBAC and audit apply. A user created by the connector is a **viewer** by default (`MCP_DEFAULT_ROLE`); raise it deliberately. User management is never exposed over MCP.
- `dispense_key` hands a plaintext key to the model, which is exactly the "private data" ingredient of the [lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/): combined in one conversation with untrusted content and a way to send data out, a prompt injection can make the model leak it. The tool is kept; its risk, the mitigations and their limits are written down in [`connector.md`](connector.md#dispense_key-and-the-lethal-trifecta). Every MCP tool carries MCP annotations (read-only / destructive / open-world hints); they are hints for clients, not enforcement.
- FastMCP issues its own short-lived JWTs to Claude and never forwards the upstream provider token; `Host`/`Origin` are validated.

## Key exposure surface

The plaintext key only ever exists:
1. In memory during registration/validation/dispensing.
2. In the response of `GET /api/v1/keys/dispense` (role `manager`+, rate-limited and audited) and of the `dispense_key` MCP tool (role `manager`+ and audited; the REST rate limit does not apply to it). What the model does with a key it received over MCP is outside this service: see ["dispense_key" and the lethal trifecta](connector.md#dispense_key-and-the-lethal-trifecta).

It is never written to logs (structured logging with no request bodies), never returned in listings, and never leaves via `/metrics`.

## Other measures

- **Rate limiting** per client address (slowapi). `RATE_LIMIT_DEFAULT` (120/minute) applies to every API and dashboard route that has no limit of its own, counted per client address and per route (all `/keys/<id>` requests share one budget); login (`RATE_LIMIT_AUTH`) and dispensing (`RATE_LIMIT_DISPENSE`) have their own, independent limits; `/health` is exempt because the platform probes it and restarts the instance when the probe fails. Over the limit the answer is `429`. The address is the one described in [`deployment.md`](deployment.md#client-ip-and-proxy-headers). The connector's own endpoints (`/mcp` and the OAuth endpoints) are not covered by these limits.
- **Immutable audit**: actor, action, resource, IP and detail of every sensitive operation (login, key register/rotate/revoke/dispense, user and project changes).
- **Dashboard hardening**: the dashboard is a static page that renders every API value (key and project names, audit actors, IP addresses…) as text with `textContent`, never as HTML, and it is served with a strict `Content-Security-Policy` (`default-src 'self'`, no inline script or style, `object-src 'none'`, `base-uri 'none'`, `frame-ancestors 'none'`). A hostile key or project name therefore cannot run script in an administrator's browser.
- **Secrets are checked at start-up.** Outside `ENVIRONMENT=development` the service refuses to start if `JWT_SECRET` or `ENCRYPTION_MASTER_KEY` (or `MCP_OAUTH_JWT_SIGNING_KEY`, when set) is empty, a placeholder from the examples, or shorter than 32 characters. An example `FIRST_ADMIN_PASSWORD` is refused only when it would be used, that is, to create the first administrator on an empty installation; once there are users it is ignored with a warning. Without it the built-in defaults would make the stored ciphertext decryptable and the session tokens forgeable by anyone who has read the repository. See [`deployment.md`](deployment.md#secrets).
- **`/metrics` is not public.** It needs an administrator's access token or, for a Prometheus scraper, `Authorization: Bearer <METRICS_TOKEN>` (compared in constant time; `METRICS_TOKEN` is held to the same start-up checks as the other secrets when set). `/docs` and `/openapi.json` stay public: they describe the API but contain no secret or deployment data.
- **`X-Request-ID` header** and per-request correlated JSON logging.
- **Non-root** container, slim multi-stage image, no secrets baked into the image.
- **CORS is closed by default.** `CORS_ORIGINS` (a JSON list, e.g. `["https://app.example.com"]`) is empty unless you set it: the dashboard is served by this same service and needs no CORS. Listed origins may send credentials; a `"*"` entry is honoured but then credentials are not allowed, because Starlette would otherwise answer a credentialed request from any website by echoing its origin back with `Access-Control-Allow-Credentials: true`. (The previous default was `["*"]` with credentials, which behaved exactly like that.)
- **Client address behind a proxy.** Real client IPs come from `X-Forwarded-For`, which uvicorn believes only from the peers listed in `FORWARDED_ALLOW_IPS` (default `127.0.0.1`, never `*`) and reads from the right; see [`deployment.md`](deployment.md#client-ip-and-proxy-headers).
- Strict input validation with Pydantic (lengths, formats, `nvapi-` prefix).

## NVIDIA service compliance

- Single outbound call: `GET {NVIDIA_VALIDATION_URL}` (default `https://integrate.api.nvidia.com/v1/models`), authenticated with the key itself — the official, read-only mechanism to check validity.
- No portal scraping, no automation of creation/rotation (no official API exists), and no sharing of keys across accounts: the system manages **your own account's** keys only. Because it is self-hosted, each operator runs their own instance with their own keys.

## Operational recommendations

- Rotate `ENCRYPTION_MASTER_KEY` only with a re-encryption procedure (export → re-encrypt → import).
- Use 12+ character passwords and enable 2FA on GitHub/Render.
- Review the audit log (`GET /api/v1/audit`) periodically.
- Configure key expiry in NVIDIA where possible and record `expires_at` here to receive the `expiring_soon` warning.
- Use a short-lived, least-privilege OAuth App for the connector and keep `MCP_ALLOWED_IDENTITIES` tight.

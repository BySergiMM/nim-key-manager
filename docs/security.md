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
- Safe bootstrap: the first user (via environment variable or first registration) is admin; afterwards registration is restricted to admins.

## Claude connector (MCP) security

- The `/mcp` endpoint is protected by **OAuth 2.1** (Authorization Code + PKCE) via GitHub or Google. Claude cannot paste a bearer token — it must complete the OAuth flow.
- An **allow-list** (`MCP_ALLOWED_IDENTITIES`) gates who may connect; it is **fail-closed** (empty = deny everyone).
- The OAuth identity maps to an application user, so the same RBAC and audit apply. Set `MCP_DEFAULT_ROLE=viewer`/`manager` to reduce what the connector can do; user management is never exposed over MCP.
- FastMCP issues its own short-lived JWTs to Claude and never forwards the upstream provider token; `Host`/`Origin` are validated.

## Key exposure surface

The plaintext key only ever exists:
1. In memory during registration/validation/dispensing.
2. In the response of `GET /api/v1/keys/dispense` and the `dispense_key` MCP tool (role `manager`+, rate-limited and audited).

It is never written to logs (structured logging with no request bodies), never returned in listings, and never leaves via `/metrics`.

## Other measures

- **Rate limiting** per IP (slowapi): global, login and dispensing with independent limits.
- **Immutable audit**: actor, action, resource, IP and detail of every sensitive operation (login, key register/rotate/revoke/dispense, user and project changes).
- **`X-Request-ID` header** and per-request correlated JSON logging.
- **Non-root** container, slim multi-stage image, no secrets baked into the image.
- Configurable CORS; `--proxy-headers` for real client IPs behind Render's proxy.
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

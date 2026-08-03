# MCP: local (stdio) and remote (OAuth)

NIM Key Manager **is** an MCP server. It speaks two transports over the same tools,
the same roles and the same audit trail:

| | **stdio** (default) | **HTTP + OAuth 2.1** (this document) |
|---|---|---|
| Who runs it | your client, as a child process | you, as a deployed service |
| Reachable from | that one machine | anywhere, over HTTPS |
| Authentication | the operating system | GitHub/Google, plus an allow-list |
| Setup | `nimkm mcp setup` | this document |
| Ports opened | none | one |

**If your client runs on the same machine as your keys, you want stdio** — one command,
no OAuth app, no public URL:

```bash
nimkm mcp setup      # detects Claude Code / Claude Desktop, backs up, registers
```

Read on only if you need Claude on the web or on your phone to reach a *deployed*
instance. Both transports can be enabled at once; they share one database.

## How remote authentication works

Claude custom connectors speak **OAuth 2.1** (Authorization Code + PKCE) and
expect Dynamic Client Registration. Claude does **not** support pasting a bearer
token or putting a token in the URL. This project therefore authenticates the
"Connect" flow against **GitHub** (default) or **Google** via FastMCP's OAuth
proxy, then maps your identity to an application user so RBAC and the audit trail
apply exactly as they do over REST.

Only identities on the **allow-list** (`MCP_ALLOWED_IDENTITIES`) may use the
connector. It is *fail-closed*: an empty list denies everyone.

```
Claude ──OAuth 2.1/PKCE──▶  ‹PUBLIC_BASE_URL›/authorize ──▶ GitHub/Google login
      ◀── short-lived JWT ── FastMCP  ◀── allow-list + user mapping ── your DB
Claude ──Bearer JWT──────▶  ‹PUBLIC_BASE_URL›/mcp   (tools)
```

## The short version

```bash
nimkm mcp oauth     # prompts for the OAuth credentials and writes them
nimkm mcp status    # endpoint, provider and allow-list
```

`nimkm mcp oauth` prints the exact callback URL to paste into the OAuth app and stores
the result in `config.env`. The rest of this document explains what it is doing and
covers the deployed (Render) case, where the same values are set as environment
variables instead.

Until credentials exist the connector is **idle**: `/mcp` is not mounted and the REST
API, dashboard and health check run normally. Nothing crashes because the connector is
unconfigured.

## Prerequisites

- The service reachable over HTTPS at a stable URL (see [`deployment.md`](deployment.md)),
  started with `nimkm web` (or the container image, which does it for you).
  On Render the public URL is injected automatically as `RENDER_EXTERNAL_URL`;
  `PUBLIC_BASE_URL` is derived from it, so you normally don't set it by hand. For a
  laptop install, put a tunnel in front (`cloudflared tunnel --url http://localhost:8000`)
  and set `PUBLIC_BASE_URL` to the tunnel URL — Claude connects from the internet, not
  from your machine. (If the client is *on* that laptop, use stdio instead.)
- A GitHub account (default) or a Google Cloud project (alternative).

## Step 1 — Create the OAuth application

### GitHub (default)

1. Go to **GitHub → Settings → Developer settings → OAuth Apps → New OAuth App**
   (<https://github.com/settings/developers>).
2. Fill in:
   - **Application name**: e.g. `NIM Key Manager (Claude)`
   - **Homepage URL**: `‹PUBLIC_BASE_URL›` (e.g. `https://nim-key-manager.onrender.com`)
   - **Authorization callback URL**: `‹PUBLIC_BASE_URL›/auth/callback`
3. Create the app, then **Generate a new client secret**. Copy the **Client ID**
   and **Client Secret**.

### Google (alternative)

1. In **Google Cloud Console → APIs & Services → Credentials**, create an
   **OAuth client ID** of type *Web application*.
2. **Authorized redirect URI**: `‹PUBLIC_BASE_URL›/auth/callback`.
3. Copy the **Client ID** and **Client Secret**, and set `MCP_AUTH_PROVIDER=google`.

> The callback path must match exactly. It defaults to `/auth/callback`.

## Step 2 — Configure the service

Set these environment variables (on Render: **Dashboard → your service →
Environment**; they are already declared in `render.yaml` as
`sync: false`, so Render will prompt you):

| Variable | Value |
| --- | --- |
| `MCP_AUTH_PROVIDER` | `github` (or `google`) |
| `MCP_GITHUB_CLIENT_ID` / `MCP_GOOGLE_CLIENT_ID` | Client ID from Step 1 |
| `MCP_GITHUB_CLIENT_SECRET` / `MCP_GOOGLE_CLIENT_SECRET` | Client Secret from Step 1 |
| `MCP_ALLOWED_IDENTITIES` | Your GitHub login and/or e-mail, comma-separated (e.g. `your-login,you@example.com`) |

Already set for you by the blueprint: `MCP_ENABLED=true`, `MCP_AUTH_ENABLED=true`,
`MCP_OAUTH_JWT_SIGNING_KEY` (generated). Save and let the service redeploy.

Optional hardening / convenience:

- `MCP_DEFAULT_ROLE` (default `admin`) — role granted to an allow-listed identity
  on first login. Set to `manager` or `viewer` to reduce what Claude can do.
- `MCP_AUTO_PROVISION` (default `true`) — if `false`, the identity must already
  match an existing app user by e-mail.
- `MCP_REDIS_URL` + `MCP_STORAGE_ENCRYPTION_KEY` — persist OAuth clients/tokens
  across restarts so you don't re-authorize after each redeploy (see Troubleshooting).

## Step 3 — Verify the endpoints

After deploy, these should all respond (replace the host):

```bash
BASE=https://nim-key-manager.onrender.com
curl -s $BASE/.well-known/oauth-authorization-server | jq .issuer
curl -s $BASE/.well-known/oauth-protected-resource/mcp | jq .resource
curl -si -X POST $BASE/mcp -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -n 1   # 401 (expected)
```

A `401` on `/mcp` with a `WWW-Authenticate: Bearer resource_metadata=...` header is
correct — it tells Claude where to discover the OAuth server.

## Step 4 — Add the connector in Claude

1. In Claude, open **Settings → Connectors → Add custom connector**.
2. **Name**: `NIM Key Manager`.
3. **Remote MCP server URL**: `‹PUBLIC_BASE_URL›/mcp`
   (e.g. `https://nim-key-manager.onrender.com/mcp`).
4. Save, then click **Connect**. Claude opens the GitHub/Google login; approve it.
5. Ask Claude to call `whoami` to confirm it is acting as your admin user.

You can now ask things like *"dispense an available NVIDIA key for project X"* or
*"which of my keys expire in the next week?"*.

## Tool reference

| Tool | Min. role | Notes |
| --- | --- | --- |
| `whoami` | viewer | Identity/role this connector acts as |
| `list_keys`, `get_key` | viewer | Metadata only — never the secret |
| `stats_overview`, `usage_stats` | viewer | Inventory and usage |
| `list_projects`, `get_project` | viewer | |
| `dispense_key` | manager | Returns a **plaintext** key (LRU, audited) |
| `register_key` | manager | Paste a key from build.nvidia.com |
| `validate_key` | manager | Checks against NVIDIA's read-only endpoint |
| `rotate_key` | manager | Assisted rotation (old key revoked) |
| `revoke_key` | manager | Destructive, reversible by re-registering |
| `check_expirations` | manager | Expire past-due keys |
| `create_project`, `update_project`, `delete_project`, `assign_key_to_project` | manager | |
| `delete_key` | admin | Permanent, irreversible |
| `list_audit` | admin | Immutable audit log |

User management is intentionally **not** exposed over MCP; manage users via the
dashboard or REST API.

## Security notes

- **Allow-list first**: only `MCP_ALLOWED_IDENTITIES` can authenticate; empty = deny all.
- **Least privilege**: set `MCP_DEFAULT_ROLE=viewer` or `manager` if you don't want
  Claude to delete keys or read the audit log.
- **Audit**: every dispense/register/rotate/revoke/delete is recorded with the
  acting user and is queryable via `list_audit`.
- **Secrets**: keys are stored AES-256-GCM encrypted; only `dispense_key` ever
  returns plaintext, and that response is never logged.
- **Transport**: FastMCP validates `Host`/`Origin`; `MCP_ALLOWED_HOSTS` derives from
  `PUBLIC_BASE_URL`.

## Troubleshooting

- **`421 Misdirected Request` on `/mcp`** — the request `Host` isn't allow-listed.
  Ensure `PUBLIC_BASE_URL`/`RENDER_EXTERNAL_URL` matches the host Claude uses, or set
  `MCP_ALLOWED_HOSTS` explicitly.
- **Connector shows a config error in Claude** — confirm the URL ends in `/mcp`, the
  OAuth callback is `‹PUBLIC_BASE_URL›/auth/callback`, and the client ID/secret are set.
- **Have to re-authorize after every redeploy** — by default OAuth clients/tokens are
  in memory. Set `MCP_REDIS_URL` and `MCP_STORAGE_ENCRYPTION_KEY` (a Fernet key) to
  persist them (`py-key-value-aio[redis]` is required for the Redis store).
- **`identity is not allowed`** — add your GitHub login/e-mail to `MCP_ALLOWED_IDENTITIES`.

## Appendix: how the stdio transport differs

`nimkm mcp serve` is the same tool surface with a different trust model, and it is worth
being explicit about it:

- **No OAuth, by design.** The client spawns the server as a child process with the
  user's own permissions; there is no socket and nothing to authenticate *to*. Anything
  able to talk to it could already read `config.env`.
- **It acts as a local administrator.** A dedicated account, `local@nimkm.internal`, is
  created on first use (`.internal` is IANA-reserved, so the address can never resolve).
  Use `--identity you@example.com` to act as a different, existing account — for example
  one with the `viewer` role, if you want the client to be read-only.
- **stdout carries protocol only.** All logging is redirected to stderr; if your client
  reports a parse error, check that nothing in your shell profile prints on start-up.
- **No background jobs.** The scheduler stays off in a client-spawned process; expiry
  checks and validation sweeps run in `nimkm web` or the container.

To watch what a session is doing, run the command yourself and type at it — it waits
silently for JSON-RPC on stdin and logs to stderr:

```bash
nimkm mcp serve
```

# Claude MCP Connector

NIM Key Manager ships a **Model Context Protocol (MCP) server** so you can add it
to Claude as a **custom connector**. Once connected, Claude can list and inspect
your NVIDIA keys, request a ready-to-use key, register/rotate/revoke keys and
manage projects — all through the same audited, role-checked services as the REST
API.

The connector is served by the *same* deployment at `‹PUBLIC_BASE_URL›/mcp`. No
separate service to run.

**The connector is optional.** Until the OAuth credentials of Step 2 are set, the
service starts normally with the REST API and the dashboard, `/mcp` answers `404`,
and the log carries a warning (`mcp_connector_not_configured`) naming the variables
that are missing. Nothing else depends on it.

## How authentication works

Claude custom connectors speak **OAuth 2.1** (Authorization Code + PKCE) and
expect Dynamic Client Registration. Claude does **not** support pasting a bearer
token or putting a token in the URL. This project therefore authenticates the
"Connect" flow against **GitHub** (default) or **Google** via FastMCP's OAuth
proxy, then maps your identity to an application user so RBAC and the audit trail
apply exactly as they do over REST.

Only identities on the **allow-list** (`MCP_ALLOWED_IDENTITIES`) may use the
connector. It is *fail-closed*: an empty list denies everyone. Entries are stable
account ids and e-mail addresses; GitHub **logins are not accepted** (see
[below](#migrating-an-existing-allow-list)).

```
Claude ──OAuth 2.1/PKCE──▶  ‹PUBLIC_BASE_URL›/authorize ──▶ GitHub/Google login
      ◀── short-lived JWT ── FastMCP  ◀── allow-list + user mapping ── your DB
Claude ──Bearer JWT──────▶  ‹PUBLIC_BASE_URL›/mcp   (tools)
```

## Prerequisites

- The service deployed and reachable over HTTPS (see [`deployment.md`](deployment.md)).
  On Render the public URL is injected automatically as `RENDER_EXTERNAL_URL`;
  `PUBLIC_BASE_URL` is derived from it, so you normally don't set it by hand.
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
| `MCP_ALLOWED_IDENTITIES` | Who may connect, comma-separated: your **numeric GitHub user id** (e.g. `98814441`; get it with `curl -s https://api.github.com/users/YOUR-LOGIN \| jq .id`) and/or an e-mail address. For Google: your verified e-mail, or the account's `sub` |

Already set for you by the blueprint: `MCP_ENABLED=true`, `MCP_AUTH_ENABLED=true`,
`MCP_OAUTH_JWT_SIGNING_KEY` (generated). Save and let the service redeploy. If the
Client ID/Secret are still blank, the connector stays off (see above) rather than
stopping the service; a connector that has credentials but no public URL
(`PUBLIC_BASE_URL` / `RENDER_EXTERNAL_URL`) does refuse to start.

Optional hardening / convenience:

- `MCP_DEFAULT_ROLE` (default `viewer`) — role of the app user created for an
  allow-listed identity on its first connection. A viewer can read key metadata but
  cannot dispense, register or delete keys. Set `manager` (or `admin`) if you want
  Claude to do more, or promote that user through the API (`PATCH /api/v1/users/{id}`).
- `MCP_AUTO_PROVISION` (default `true`) — if `false`, the identity must already
  match an existing app user by e-mail.
- `MCP_REDIS_URL` + `MCP_STORAGE_ENCRYPTION_KEY` — persist OAuth clients/tokens
  across restarts so you don't re-authorize after each redeploy (see Troubleshooting).

### Migrating an existing allow-list

Before this change the allow-list accepted GitHub **logins**, and the first connection
created an **admin**. Both were unsafe: a login is not an identity (the owner can rename
the account and anybody can then register the freed name, which passed the check), and an
admin connector can dispense and delete every key.

- **Logins no longer match.** At start-up the log says so
  (`mcp_allow_list_entries_ignored`, listing the entries) and, if nothing usable is left,
  `mcp_allow_list_empty`. Replace each login by the numeric id of that account:
  `curl -s https://api.github.com/users/YOUR-LOGIN | jq .id`. E-mail entries keep working.
  If you would rather have the service tell you: connect once from Claude, then read the
  `mcp_identity_denied` line in the log; its `subject` is the id to add. (The person who is
  denied is not told the id.)
- **The default role is now `viewer`.** Users that already exist keep their role. Only the
  users created from now on start as viewers. To give the connector more, set
  `MCP_DEFAULT_ROLE=manager` before the first connection, or promote the user afterwards
  with an admin token: `PATCH /api/v1/users/{id}` and `{"role": "manager"}` (see `/docs`).
- **E-mail entries.** Google: an address only matches when Google reports it as verified
  (`email_verified`). GitHub: the public profile e-mail matches (GitHub only lets you publish
  one of your verified addresses; that is an inference from its documentation, the API does not
  say so). An e-mail address can change hands, a numeric id cannot, so prefer ids. An
  address the provider reports as unverified is never used to find the application user, so an
  account cannot borrow somebody else's role by claiming their address.

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

**Argument limits.** The tools reject out-of-range arguments before they run, with the same
bounds as the REST API (and they are published in each tool's input schema): `usage_stats`
`days` 1–365; `list_audit` `limit` 1–500, `offset` 0 to 2,147,483,647, `action` up to 60
characters; key and project `name` 1–120 characters; project `description` up to 2,000;
ids up to 64 characters; `expires_at` up to 64; API keys 20–512 characters starting with
`nvapi-`. A rejected key is never echoed back or logged.

## Security notes

- **Allow-list first**: only `MCP_ALLOWED_IDENTITIES` can authenticate; empty = deny all.
  It matches stable account ids (and e-mails the provider vouches for), never logins.
- **Least privilege**: users created by the connector are **viewers** unless you set
  `MCP_DEFAULT_ROLE`; keep it at `viewer` or `manager` if you don't want Claude to
  delete keys or read the audit log.
- **Audit**: every dispense/register/rotate/revoke/delete is recorded with the
  acting user and is queryable via `list_audit`.
- **Secrets**: keys are stored AES-256-GCM encrypted; only `dispense_key` ever
  returns plaintext, and that response is never logged.
- **Transport**: FastMCP validates `Host`/`Origin`; `MCP_ALLOWED_HOSTS` derives from
  `PUBLIC_BASE_URL`.

## Troubleshooting

- **`404` on `/mcp`** — the connector is off because the OAuth client ID/secret of the
  selected provider are not both set (or `MCP_ENABLED=false`). Check the service log for
  `mcp_connector_not_configured`.
- **`421 Misdirected Request` on `/mcp`** — the request `Host` isn't allow-listed.
  Ensure `PUBLIC_BASE_URL`/`RENDER_EXTERNAL_URL` matches the host Claude uses, or set
  `MCP_ALLOWED_HOSTS` explicitly.
- **Connector shows a config error in Claude** — confirm the URL ends in `/mcp`, the
  OAuth callback is `‹PUBLIC_BASE_URL›/auth/callback`, and the client ID/secret are set.
- **Have to re-authorize after every redeploy** — by default OAuth clients/tokens are
  in memory. Set `MCP_REDIS_URL` and `MCP_STORAGE_ENCRYPTION_KEY` (a Fernet key) to
  persist them (`py-key-value-aio[redis]` is required for the Redis store).
- **`identity is not allowed`** — add your numeric account id (or e-mail) to
  `MCP_ALLOWED_IDENTITIES`. A GitHub login does not work; the `mcp_identity_denied` log
  line shows the id of the account that was refused.

## Local development (no OAuth)

For local testing you can disable auth — **never do this on a public deployment**:

```bash
ENVIRONMENT=development MCP_AUTH_ENABLED=false PUBLIC_BASE_URL=http://localhost:8000 \
  uvicorn app.main:create_asgi_app --factory
```

(`ENVIRONMENT=development` is what lets the service start without real secrets; see
[Secrets](deployment.md#secrets).)

With auth disabled the actor resolves to `MCP_DEV_IDENTITY` (or the first admin).

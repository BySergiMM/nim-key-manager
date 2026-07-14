# API examples

Base: `https://your-service.onrender.com`. Authentication: `Authorization: Bearer <access_token>`. Interactive OpenAPI at `/docs`.

## Authentication

```bash
# Log in
curl -s $BASE/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"..."}'
# → {"access_token":"...","refresh_token":"...","token_type":"bearer"}

# Refresh tokens
curl -s $BASE/api/v1/auth/refresh -H 'Content-Type: application/json' \
  -d '{"refresh_token":"..."}'

# Current user
curl -s $BASE/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
```

## Users (admin)

```bash
# Create a user with a role
curl -s $BASE/api/v1/users -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"email":"dev@example.com","password":"Password123!","role":"manager"}'

# Change role / deactivate
curl -s -X PATCH $BASE/api/v1/users/$USER_ID -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"role":"viewer","is_active":false}'
```

## API keys

```bash
# Register a key (previously created at build.nvidia.com/settings/api-keys)
# validate_remote=true checks it against NVIDIA before accepting it
curl -s $BASE/api/v1/keys -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"prod-1","api_key":"nvapi-...","validate_remote":true,"expires_at":"2026-12-31T23:59:59Z"}'

# List (never returns the plaintext key; filters: ?status=active&project_id=...)
curl -s $BASE/api/v1/keys -H "Authorization: Bearer $TOKEN"

# Validate on demand against NVIDIA
curl -s -X POST $BASE/api/v1/keys/$KEY_ID/validate -H "Authorization: Bearer $TOKEN"

# Rotate (paste the NEW key; the old one is revoked and linked)
curl -s -X POST $BASE/api/v1/keys/$KEY_ID/rotate -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"api_key":"nvapi-NEW..."}'

# Revoke / delete (delete requires admin)
curl -s -X POST $BASE/api/v1/keys/$KEY_ID/revoke -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE $BASE/api/v1/keys/$KEY_ID -H "Authorization: Bearer $TOKEN"

# On-demand expiry check
curl -s -X POST $BASE/api/v1/keys/maintenance/expiry-check -H "Authorization: Bearer $TOKEN"
```

## Dispensing (integrate with your apps)

```bash
# Least-recently-used active key, globally or per project
curl -s "$BASE/api/v1/keys/dispense" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/v1/keys/dispense?project_id=$PROJECT_ID" -H "Authorization: Bearer $TOKEN"
# → {"key_id":"...","name":"prod-1","key_hint":"…f3a2","project_id":null,"api_key":"nvapi-..."}
```

Consuming it from Python:

```python
import httpx
from openai import OpenAI

resp = httpx.get(
    f"{BASE}/api/v1/keys/dispense",
    headers={"Authorization": f"Bearer {token}"},
    params={"project_id": project_id},
)
resp.raise_for_status()
nvidia_key = resp.json()["api_key"]

client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=nvidia_key)
```

## Projects

```bash
curl -s $BASE/api/v1/projects -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"chatbot","description":"Support bot"}'
curl -s -X POST $BASE/api/v1/projects/$PROJECT_ID/keys/$KEY_ID -H "Authorization: Bearer $TOKEN"
```

## Stats and audit

```bash
curl -s $BASE/api/v1/stats/overview -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/v1/stats/usage?days=30" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/v1/audit?limit=50&action=key.dispensed" -H "Authorization: Bearer $TOKEN"  # admin
```

## Claude connector (MCP)

Prefer natural language through Claude once the connector is added (see [`connector.md`](connector.md)). Example prompts:

- "Dispense an available NVIDIA key for the `chatbot` project."
- "Which of my keys expire in the next 7 days?"
- "Register this key and attach it to project `chatbot`: `nvapi-...`"
- "Rotate key `‹id›` with this new one: `nvapi-...`"

# Ejemplos de API

Base: `https://tu-servicio.onrender.com`. Autenticación: `Authorization: Bearer <access_token>`. OpenAPI interactivo en `/docs`.

## Autenticación

```bash
# Login
curl -s $BASE/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"..."}'
# → {"access_token":"...","refresh_token":"...","token_type":"bearer"}

# Renovar tokens
curl -s $BASE/api/v1/auth/refresh -H 'Content-Type: application/json' \
  -d '{"refresh_token":"..."}'

# Usuario actual
curl -s $BASE/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
```

## Usuarios (admin)

```bash
# Crear usuario con rol
curl -s $BASE/api/v1/users -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"email":"dev@example.com","password":"Password123!","role":"manager"}'

# Cambiar rol / desactivar
curl -s -X PATCH $BASE/api/v1/users/$USER_ID -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"role":"viewer","is_active":false}'
```

## API Keys

```bash
# Registrar una key (creada previamente en build.nvidia.com/settings/api-keys)
# validate_remote=true la comprueba contra NVIDIA antes de aceptarla
curl -s $BASE/api/v1/keys -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"prod-1","api_key":"nvapi-...","validate_remote":true,"expires_at":"2026-12-31T23:59:59Z"}'

# Listar (nunca devuelve la key en claro; filtros: ?status=active&project_id=...)
curl -s $BASE/api/v1/keys -H "Authorization: Bearer $TOKEN"

# Validar bajo demanda contra NVIDIA
curl -s -X POST $BASE/api/v1/keys/$KEY_ID/validate -H "Authorization: Bearer $TOKEN"

# Rotar (pega la key NUEVA; la antigua queda revocada y enlazada)
curl -s -X POST $BASE/api/v1/keys/$KEY_ID/rotate -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"api_key":"nvapi-NUEVA..."}'

# Revocar / eliminar (eliminar requiere admin)
curl -s -X POST $BASE/api/v1/keys/$KEY_ID/revoke -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE $BASE/api/v1/keys/$KEY_ID -H "Authorization: Bearer $TOKEN"

# Comprobación de expiraciones bajo demanda
curl -s -X POST $BASE/api/v1/keys/maintenance/expiry-check -H "Authorization: Bearer $TOKEN"
```

## Dispensación (integración con tus aplicaciones)

```bash
# Key activa menos usada, global o por proyecto
curl -s "$BASE/api/v1/keys/dispense" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/v1/keys/dispense?project_id=$PROJECT_ID" -H "Authorization: Bearer $TOKEN"
# → {"key_id":"...","name":"prod-1","key_hint":"…f3a2","project_id":null,"api_key":"nvapi-..."}
```

Ejemplo de consumo desde Python:

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

## Proyectos

```bash
curl -s $BASE/api/v1/projects -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"chatbot","description":"Bot de soporte"}'
curl -s -X POST $BASE/api/v1/projects/$PROJECT_ID/keys/$KEY_ID -H "Authorization: Bearer $TOKEN"
```

## Estadísticas y auditoría

```bash
curl -s $BASE/api/v1/stats/overview -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/v1/stats/usage?days=30" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/v1/audit?limit=50&action=key.dispensed" -H "Authorization: Bearer $TOKEN"  # admin
```

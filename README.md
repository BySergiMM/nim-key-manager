# NIM Key Manager

Gestor de ciclo de vida para las **API Keys de tu cuenta de NVIDIA Build/NIM**: almacenamiento cifrado, rotación asistida, detección de expiraciones, estadísticas de uso, proyectos, RBAC, auditoría, dashboard web y **conector para Claude (MCP)**. Listo para producción y con despliegue automático en la nube.

> **Cumplimiento de los ToS de NVIDIA**: NVIDIA Build **no ofrece API pública oficial** para crear o rotar keys programáticamente (se generan manualmente en [build.nvidia.com/settings/api-keys](https://build.nvidia.com/settings/api-keys)). Este proyecto solo realiza la operación oficial disponible — **validación de la key** contra el endpoint de solo lectura `GET https://integrate.api.nvidia.com/v1/models` — y gestiona el resto del ciclo de vida (cifrado, inventario, rotación asistida, expiración, dispensación) del lado del servidor propio. No automatiza ni scrapea el portal de NVIDIA.

## Características

- **Registro seguro de keys**: cifradas en reposo con AES-256-GCM (clave derivada por HKDF-SHA256 desde el secret manager de la plataforma). Nunca se almacenan ni se registran en logs en claro.
- **Rotación asistida y auditada**: creas la key nueva en tu cuenta NVIDIA, la pegas, y el sistema hace el swap atómico (nueva activa, antigua revocada con enlace de linaje `rotated_from_id`).
- **Detección de expiraciones**: job en background cada hora + endpoint de mantenimiento; flag `expiring_soon` configurable.
- **Validación periódica**: sweep automático contra NVIDIA cada 6 h (configurable) que marca keys inválidas/revocadas.
- **Dispensación de keys**: `GET /api/v1/keys/dispense` devuelve la key activa menos usada (LRU), global o por proyecto, registrando el uso.
- **Proyectos**: agrupa keys por consumidor/carga de trabajo.
- **Estadísticas**: inventario por estado, dispensaciones, serie temporal de uso.
- **Seguridad**: JWT (access+refresh), roles `admin`/`manager`/`viewer`, rate limiting, auditoría inmutable de cada operación sensible.
- **Conector para Claude (MCP)**: servidor MCP en `/mcp` con OAuth 2.1 (GitHub/Google) para usar el gestor desde Claude como custom connector, con el mismo RBAC y auditoría.
- **Operación**: logging estructurado JSON, métricas Prometheus en `/metrics`, healthcheck en `/health`, OpenAPI en `/docs`.

## Arquitectura

```
app/
├── domain/           # Enums y excepciones de dominio (sin dependencias)
├── application/      # Casos de uso (servicios) y puertos (interfaces)
│   └── services/     # auth, users, keys, projects, stats, audit
├── infrastructure/   # Adaptadores: SQLAlchemy (repositorios) y gateway NVIDIA
├── api/              # FastAPI: routers, schemas, deps, rate limiting
├── mcp/              # Conector Claude: servidor MCP, OAuth y mapeo de identidad
├── dashboard/        # SPA ligera servida en /
├── tasks/            # Jobs programados (APScheduler)
└── core/             # Config, crypto, seguridad, logging
```

Clean Architecture pragmática: las dependencias apuntan hacia dentro; la capa de aplicación no conoce FastAPI y accede a NVIDIA a través del puerto `KeyValidator`. Detalles y decisiones en [`docs/architecture.md`](docs/architecture.md).

**Stack**: Python 3.12 · FastAPI · FastMCP (conector Claude) · SQLAlchemy 2 (async) · PostgreSQL gestionado · Alembic · Docker · GitHub Actions · Render (Blueprint) · structlog · Prometheus · slowapi · APScheduler.

## Despliegue automático (sin ejecutar nada en local)

1. **Crea el repositorio en GitHub** y sube este código (puedes hacerlo desde la web de GitHub con "uploading an existing file", o con `gh repo create`).
2. **Crea una cuenta en [Render](https://render.com)** (la base de datos y el servicio web se aprovisionan solos).
3. En Render: **New → Blueprint** → conecta el repositorio. Render lee `render.yaml` y crea automáticamente:
   - la base de datos PostgreSQL gestionada,
   - el servicio web Docker con healthcheck,
   - los secretos `JWT_SECRET` y `ENCRYPTION_MASTER_KEY` (generados y custodiados por el secret manager de Render),
   - `DATABASE_URL` inyectada desde la base de datos.
4. Render te pedirá **una sola vez** `FIRST_ADMIN_EMAIL` y `FIRST_ADMIN_PASSWORD` (tu usuario administrador inicial, creado en el primer arranque).
5. Cada `git push` a `main` ejecuta CI (lint + tipos + tests + build) y despliega automáticamente. Las migraciones (`alembic upgrade head`) se ejecutan en el arranque del contenedor.

Opcional: añade el secreto `RENDER_DEPLOY_HOOK_URL` en GitHub (Settings → Secrets → Actions) para que el despliegue lo dispare el workflow **solo si CI pasa**. Guía completa en [`docs/deployment.md`](docs/deployment.md).

## Uso rápido de la API

```bash
BASE=https://tu-servicio.onrender.com

# Login (el admin se creó en el primer arranque)
TOKEN=$(curl -s $BASE/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"tu@email.com","password":"tu-password"}' | jq -r .access_token)

# Registrar una key creada en build.nvidia.com (validándola contra NVIDIA)
curl -s $BASE/api/v1/keys -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"prod-1","api_key":"nvapi-...","validate_remote":true}'

# Recuperar una key disponible (LRU) para usarla en tu aplicación
curl -s "$BASE/api/v1/keys/dispense" -H "Authorization: Bearer $TOKEN"
```

Más ejemplos (rotación, proyectos, estadísticas, auditoría) en [`docs/api-examples.md`](docs/api-examples.md). OpenAPI interactivo en `/docs`.

## Conector para Claude (MCP)

Además de la API REST, el servicio expone un **servidor MCP** en `‹BASE›/mcp` para añadirlo a **Claude como custom connector**. Claude autentica con **OAuth 2.1** (GitHub por defecto, Google opcional) y puede listar/inspeccionar keys, **dispensar** una key disponible, registrar/rotar/revocar y gestionar proyectos — con el mismo RBAC y auditoría que la API. Solo las identidades de `MCP_ALLOWED_IDENTITIES` pueden conectarse (fail-closed).

Puesta en marcha (resumen):

1. Crea una **OAuth App** (GitHub) con callback `‹BASE›/auth/callback` y copia Client ID/Secret.
2. En Render define `MCP_GITHUB_CLIENT_ID`, `MCP_GITHUB_CLIENT_SECRET` y `MCP_ALLOWED_IDENTITIES` (tu login/email). El resto ya viene en `render.yaml` (`PUBLIC_BASE_URL` se toma de `RENDER_EXTERNAL_URL`).
3. En Claude: **Settings → Connectors → Add custom connector** → URL `‹BASE›/mcp` → **Connect** → login GitHub.

Guía completa (Google, referencia de tools, seguridad y troubleshooting) en [`docs/connector.md`](docs/connector.md).

## Roles

| Operación | viewer | manager | admin |
|---|---|---|---|
| Ver keys, proyectos y estadísticas | ✅ | ✅ | ✅ |
| Registrar / validar / rotar / revocar keys, dispensar | ❌ | ✅ | ✅ |
| Gestionar proyectos | ❌ | ✅ | ✅ |
| Eliminar keys, gestionar usuarios, ver auditoría | ❌ | ❌ | ✅ |

## Desarrollo y tests

No es necesario para desplegar, pero está soportado:

```bash
pip install -e ".[dev]"
pytest --cov=app          # cobertura mínima exigida: 85%
ruff check . && mypy app
docker compose up         # entorno local con PostgreSQL
```

## Seguridad

Modelo de amenazas, detalles criptográficos y decisiones en [`docs/security.md`](docs/security.md). Puntos clave: cifrado AES-256-GCM con clave derivada (HKDF) del secret manager, huella SHA-256 para deduplicar sin exponer el secreto, JWT firmado con expiración corta, argon2 para contraseñas, rate limiting por IP, auditoría de toda operación sensible y ninguna key en claro en logs ni en respuestas salvo el endpoint explícito de dispensación.

## Licencia

MIT — ver [LICENSE](LICENSE).

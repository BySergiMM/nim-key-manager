# Despliegue

## Producción: Render Blueprint (recomendado, cero configuración manual)

`render.yaml` define toda la infraestructura: base de datos PostgreSQL gestionada, servicio web Docker, healthcheck, autodeploy y secretos.

1. Sube el repositorio a GitHub.
2. En [Render](https://dashboard.render.com): **New → Blueprint** → selecciona el repo → **Apply**.
3. Introduce cuando lo pida (una sola vez): `FIRST_ADMIN_EMAIL` y `FIRST_ADMIN_PASSWORD`.
4. Espera al primer deploy. La URL pública queda disponible con:
   - Dashboard: `/`
   - OpenAPI: `/docs`
   - Salud: `/health` · Métricas Prometheus: `/metrics`

Qué automatiza el blueprint:

| Recurso | Detalle |
|---|---|
| PostgreSQL gestionado | `nim-key-manager-db`, backups de Render |
| `DATABASE_URL` | Inyectada desde la BD (normalización automática a asyncpg) |
| `JWT_SECRET`, `ENCRYPTION_MASTER_KEY` | Generados por el secret manager de Render |
| Migraciones | `alembic upgrade head` en cada arranque del contenedor |
| Autodeploy | En cada push a `main` |
| Healthcheck | `/health` (reinicio automático si falla) |

## CI/CD (GitHub Actions)

- **`ci.yml`** (push/PR): ruff → mypy → pytest con puerta de cobertura ≥85% → build de imagen Docker (con caché).
- **`deploy.yml`**: si defines el secreto `RENDER_DEPLOY_HOOK_URL` (Render → Settings → Deploy Hook), el deploy se dispara **solo cuando CI pasa**. Si no lo defines, actúa el autodeploy del blueprint.

Recomendación: desactiva "Auto-Deploy" en Render y usa el hook para que ningún commit roto llegue a producción.

## Monitorización y logs

- **Logs**: JSON estructurado (structlog) por stdout → visor de logs de Render (o cualquier agregador: Datadog, Grafana Loki…). Cada línea incluye `request_id`, ruta, status y duración.
- **Métricas**: `/metrics` en formato Prometheus (latencias, códigos, throughput por handler). Compatible con Grafana Cloud/Prometheus remoto.
- **Health**: `/health` comprueba la conexión a la base de datos.
- **Jobs**: expiración (cada hora) y sweep de validación contra NVIDIA (cada 6 h) registran resultados en logs y auditoría.

## Variables de entorno

Ver [`.env.example`](../.env.example) para la lista completa comentada.

## Alternativas de despliegue

La imagen es un contenedor OCI estándar (puerto `$PORT`, migraciones en el entrypoint), por lo que funciona sin cambios en Fly.io, Railway, Cloud Run o ECS: solo necesita `DATABASE_URL`, `JWT_SECRET` y `ENCRYPTION_MASTER_KEY`.

## Desarrollo local (opcional)

```bash
docker compose up --build   # app + PostgreSQL 16
# Dashboard en http://localhost:8000 (admin@example.com / admin-change-me)
```

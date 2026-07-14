#!/bin/sh
set -e

echo "==> Running database migrations"
alembic upgrade head

echo "==> Starting NIM Key Manager (REST + dashboard + Claude MCP connector)"
exec uvicorn app.main:create_asgi_app --factory \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips "*"

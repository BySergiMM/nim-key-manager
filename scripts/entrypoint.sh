#!/bin/sh
# Container entrypoint.
#
# Fully configured deployments (Render, Fly, Kubernetes, docker-compose) provide
# DATABASE_URL and the secrets through the environment and this script simply
# migrates and starts. A bare `docker run` provides nothing, so the container
# self-provisions into /data instead of crash-looping on missing configuration.
set -e

DATA_DIR="${NIMKM_HOME:-/data}"
SECRETS_FILE="$DATA_DIR/secrets.env"

mkdir -p "$DATA_DIR"

if [ -z "${DATABASE_URL:-}" ]; then
    export DATABASE_URL="sqlite+aiosqlite:///$DATA_DIR/nimkm.db"
    echo "==> DATABASE_URL not set; using the bundled SQLite database at $DATA_DIR/nimkm.db"
    echo "    Mount a volume at $DATA_DIR (or set DATABASE_URL) to keep your data."
fi

if [ -z "${JWT_SECRET:-}" ] || [ -z "${ENCRYPTION_MASTER_KEY:-}" ]; then
    if [ ! -f "$SECRETS_FILE" ]; then
        echo "==> Generating secrets into $SECRETS_FILE (keep this volume!)"
        umask 077
        python - "$SECRETS_FILE" <<'PY'
import secrets
import sys

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    for name in ("JWT_SECRET", "ENCRYPTION_MASTER_KEY", "MCP_OAUTH_JWT_SIGNING_KEY"):
        handle.write(f"{name}={secrets.token_urlsafe(48)}\n")
PY
    fi
    # shellcheck disable=SC1090
    . "$SECRETS_FILE"
    export JWT_SECRET ENCRYPTION_MASTER_KEY MCP_OAUTH_JWT_SIGNING_KEY
    echo "==> Secrets loaded from $SECRETS_FILE"
    echo "    Losing this file makes every stored API key undecryptable."
fi

echo "==> Running database migrations"
nimkm migrate

if [ -n "${FIRST_ADMIN_EMAIL:-}" ] && [ -n "${FIRST_ADMIN_PASSWORD:-}" ]; then
    :   # the application seeds this administrator on first start
else
    echo "==> No FIRST_ADMIN_EMAIL/FIRST_ADMIN_PASSWORD set."
    echo "    Create one with: docker exec -it <container> nimkm admin create --email you@example.com"
fi

echo "==> Starting NIM Key Manager (dashboard + REST API + remote MCP connector)"
exec nimkm web --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers

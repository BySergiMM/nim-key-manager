#!/bin/sh
set -e

echo "==> Running database migrations"
alembic upgrade head

# uvicorn only believes X-Forwarded-For / X-Forwarded-Proto from the peers listed in
# --forwarded-allow-ips, and then takes the RIGHT-most X-Forwarded-For entry that is not one
# of those peers. The address it derives is what the rate limiter and the audit log key on, so
# this must never be "*": "*" trusts every peer and takes the LEFT-most entry, which the client
# writes itself. The default trusts only a reverse proxy on this host; behind a platform load
# balancer set FORWARDED_ALLOW_IPS to its address range (see
# docs/deployment.md#client-ip-and-proxy-headers).
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
case "$FORWARDED_ALLOW_IPS" in
  *\**)
    echo "WARNING: FORWARDED_ALLOW_IPS contains '*': any client can forge its IP address" \
         "(rate limits, audit log). List the proxy addresses instead." >&2
    ;;
esac
echo "==> Trusting X-Forwarded-* headers only from: $FORWARDED_ALLOW_IPS"

echo "==> Starting NIM Key Manager (REST + dashboard + Claude MCP connector)"
exec uvicorn app.main:create_asgi_app --factory \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"

"""Application-wide rate limiter (slowapi, in-memory storage)."""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.middleware import _should_exempt
from slowapi.util import get_remote_address

from app.core.config import get_settings

_settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[_settings.rate_limit_default],
    enabled=_settings.rate_limit_enabled,
    # One budget per route (the endpoint function), not per literal URL: with the default
    # "url" style, GET /keys/<one id after another> would get a fresh budget for every id and
    # the default limit would never apply to any route with a path parameter.
    key_style="endpoint",
)


async def enforce_default_limit(request: Request) -> None:
    """Apply ``RATE_LIMIT_DEFAULT`` to the route that matched the request.

    slowapi only evaluates ``default_limits`` through ``SlowAPIMiddleware`` (the
    ``@limiter.limit`` decorators cover the routes that have their own limit). That middleware
    looks the endpoint up by iterating ``app.routes``, and since FastAPI 0.137 routers added with
    ``include_router`` are no longer flattened into that list, so the middleware silently skips
    every one of them (measured with a minimal app: FastAPI 0.136.1 answers 429 on an included
    route; 0.137.0 and 0.142.2 never do). This dependency is declared on the application instead
    and takes the endpoint from ``scope["route"]``, which FastAPI sets once it has routed the
    request, so it behaves the same on every version.

    The rules are the middleware's: a route with its own ``@limiter.limit`` keeps only that
    limit, ``@limiter.exempt`` routes are skipped, and ``RATE_LIMIT_ENABLED=false`` turns it off.
    The count is per client address and per route template (``key_style="endpoint"``): every
    ``/keys/<id>`` shares one budget however many different ids are asked for.
    """
    if not limiter.enabled:
        return
    endpoint = getattr(request.scope.get("route"), "endpoint", None)
    if endpoint is not None and _should_exempt(limiter, endpoint):
        return
    # Raises RateLimitExceeded, which the application turns into a 429.
    limiter._check_request_limit(request, endpoint, True)

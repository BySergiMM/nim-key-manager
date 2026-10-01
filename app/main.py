"""Application factory and HTTP wiring."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.applications import Starlette

from app import __version__
from app.api.deps import require_metrics_access
from app.api.rate_limit import enforce_default_limit, limiter
from app.api.routers import audit, auth, health, keys, projects, stats, users
from app.core.config import get_settings, is_placeholder
from app.core.logging import configure_logging, get_logger
from app.domain.exceptions import (
    ConfigurationError,
    ConflictError,
    DecryptionError,
    DomainError,
    InvalidCredentialsError,
    InvalidTokenError,
    NoKeyAvailableError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailedError,
)
from app.infrastructure.db.session import SessionFactory, init_db

logger = get_logger(__name__)

DASHBOARD_DIR = Path(__file__).parent / "dashboard" / "static"

# The dashboard is a static shell: dashboard.js renders every API value with textContent
# only. This policy is the second layer. Even if a value ever reached the DOM as markup,
# inline script, inline handlers and third-party origins are refused, so it cannot run.
DASHBOARD_CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)
_DASHBOARD_HEADERS = {
    "Content-Security-Policy": DASHBOARD_CSP,
    "X-Content-Type-Options": "nosniff",
    # Revalidate on every load (ETag/Last-Modified still apply) so a redeploy never
    # leaves a browser pairing a new index.html with an old dashboard.js.
    "Cache-Control": "no-cache",
}

_STATUS_BY_EXCEPTION: tuple[tuple[type[DomainError], int], ...] = (
    (NotFoundError, 404),
    (NoKeyAvailableError, 404),
    (ConflictError, 409),
    (PermissionDeniedError, 403),
    (InvalidCredentialsError, 401),
    (InvalidTokenError, 401),
    (ValidationFailedError, 422),
    (DecryptionError, 500),
    (DomainError, 400),
)


async def _seed_first_admin() -> None:
    """Create the bootstrap admin from env vars when the user table is empty.

    This and ``scripts/create_admin.py`` are the only ways to get a first administrator: no
    HTTP request can create one (``POST /api/v1/auth/register`` is admin-only), so a freshly
    deployed instance cannot be taken over by whoever reaches it first. When there are no
    users and no bootstrap admin is configured the service is up but nobody can sign in, and
    this says so.
    """
    settings = get_settings()
    from app.application.services.auth_service import AuthService
    from app.infrastructure.db.repositories import UserRepository

    # Outside local development the example password is not a password: judged here, and not at
    # start-up, because it only matters while there is nobody to sign in.
    placeholder_password = (
        not settings.is_local_environment
        and settings.first_admin_password is not None
        and is_placeholder(settings.first_admin_password)
    )
    async with SessionFactory() as session:
        if await UserRepository(session).count() != 0:
            if placeholder_password:
                logger.warning(
                    "first_admin_password_ignored",
                    detail=(
                        "FIRST_ADMIN_PASSWORD is a placeholder from the example configuration; "
                        "it is ignored because the installation already has users. Remove it "
                        "from the environment"
                    ),
                )
            return
        if placeholder_password:
            raise ConfigurationError(
                "Refusing to create the first administrator: FIRST_ADMIN_PASSWORD is a "
                "placeholder from the example configuration. Set a password of your own "
                "(see docs/deployment.md#first-administrator)"
            )
        if not settings.first_admin_email or not settings.first_admin_password:
            logger.warning(
                "bootstrap_admin_not_configured",
                detail=(
                    "there are no users and FIRST_ADMIN_EMAIL / FIRST_ADMIN_PASSWORD are not "
                    "both set, so nobody can sign in: set them and restart "
                    "(see docs/deployment.md#first-administrator)"
                ),
            )
            return
        await AuthService(session).bootstrap_admin(
            email=settings.first_admin_email,
            password=settings.first_admin_password,
            full_name="Bootstrap admin",
        )
        logger.info("bootstrap_admin_created", email=settings.first_admin_email)


def _make_handler(status_code: int) -> Callable[[Request, Exception], Awaitable[Response]]:
    async def handler(request: Request, exc: Exception) -> Response:
        headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
        return JSONResponse(
            status_code=status_code,
            content={"detail": str(exc) or exc.__class__.__name__},
            headers=headers,
        )

    return handler


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.debug)
    settings.require_secure_secrets()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.auto_create_tables:
            await init_db()
        await _seed_first_admin()
        scheduler = None
        if settings.scheduler_enabled:
            from app.tasks.scheduler import build_scheduler

            scheduler = build_scheduler()
            scheduler.start()
            logger.info("scheduler_started")
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Secure lifecycle manager for NVIDIA Build/NIM API keys: encrypted storage, "
            "assisted rotation, expiry detection, usage statistics, RBAC and audit."
        ),
        lifespan=lifespan,
        # RATE_LIMIT_DEFAULT: the limit of every route that has no @limiter.limit of its own.
        dependencies=[Depends(enforce_default_limit)],
    )

    # No origin is allowed unless CORS_ORIGINS lists it. A "*" is never combined with
    # credentials: Starlette answers a credentialed request from any origin by echoing that
    # origin back with ``Access-Control-Allow-Credentials: true``, so "*" plus credentials
    # would mean "every website, with credentials". Starlette's own documentation says
    # the wildcards cannot be used where credentials are allowed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials="*" not in settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.limiter = limiter
    # slowapi's handler is typed narrowly (RateLimitExceeded), Starlette expects Exception.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    for exc_class, status_code in _STATUS_BY_EXCEPTION:
        app.add_exception_handler(exc_class, _make_handler(status_code))

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(
            request_id=request_id, method=request.method, path=request.url.path
        )
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "http_request",
                status_code=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            return response
        finally:
            structlog.contextvars.clear_contextvars()

    instrumentator = Instrumentator()
    if settings.metrics_enabled:
        try:
            instrumentator.instrument(app)
        except ValueError:  # collectors already registered (multiple apps per process)
            logger.warning("metrics_already_registered")
    # Not public: METRICS_TOKEN for a scraper, or an administrator's access token.
    instrumentator.expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
        dependencies=[Depends(require_metrics_access)],
    )

    for router in (
        health.router,
        auth.router,
        users.router,
        keys.router,
        projects.router,
        stats.router,
        audit.router,
    ):
        app.include_router(router)

    # Explicit routes (not a StaticFiles mount): exactly these three files are exposed,
    # each with the dashboard security headers and an explicit media type.
    def dashboard_asset(name: str, media_type: str) -> FileResponse:
        return FileResponse(DASHBOARD_DIR / name, media_type=media_type, headers=_DASHBOARD_HEADERS)

    @app.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return dashboard_asset("index.html", "text/html")

    @app.get("/static/dashboard.js", include_in_schema=False)
    async def dashboard_script() -> FileResponse:
        return dashboard_asset("dashboard.js", "text/javascript")

    @app.get("/static/dashboard.css", include_in_schema=False)
    async def dashboard_style() -> FileResponse:
        return dashboard_asset("dashboard.css", "text/css")

    return app


def create_asgi_app() -> Starlette:
    """Production ASGI entrypoint.

    Wraps the REST/dashboard application (:func:`create_app`) with the Claude MCP
    connector when ``MCP_ENABLED`` is set and the OAuth credentials are configured,
    exposing an OAuth-secured ``/mcp`` endpoint alongside the existing API; without the
    credentials the connector stays off and the rest of the service runs.
    ``create_app`` is left untouched so the test suite keeps exercising the pure
    FastAPI app.
    """
    from app.mcp.asgi import mount_mcp_connector

    return mount_mcp_connector(create_app(), get_settings())

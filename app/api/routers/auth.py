"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_current_user, get_optional_user
from app.api.rate_limit import limiter
from app.api.schemas import LoginRequest, RefreshRequest, TokenPair, UserCreate, UserOut
from app.application.services.auth_service import AuthService
from app.core.config import get_settings
from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_session

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_settings = get_settings()


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: UserCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(get_optional_user),
) -> User:
    """Bootstrap: the first user self-registers as admin; afterwards admin-only."""
    return await AuthService(session).register(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=body.role,
        actor=actor,
        ip_address=client_ip(request),
    )


@router.post("/login", response_model=TokenPair)
@limiter.limit(_settings.rate_limit_auth)
async def login(
    request: Request,
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenPair:
    access, refresh = await AuthService(session).login(
        email=body.email, password=body.password, ip_address=client_ip(request)
    )
    return TokenPair(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    body: RefreshRequest, session: AsyncSession = Depends(get_session)
) -> TokenPair:
    access, refresh_token = await AuthService(session).refresh(body.refresh_token)
    return TokenPair(access_token=access, refresh_token=refresh_token)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user

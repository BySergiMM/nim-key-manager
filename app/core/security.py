"""Password hashing (argon2) and JWT issuance/verification."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings
from app.domain.exceptions import InvalidTokenError

_password_hash = PasswordHash.recommended()

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _password_hash.verify(password, hashed)


def create_token(subject: str, role: str, token_type: TokenType) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    if token_type == "access":
        expires = now + timedelta(minutes=settings.access_token_expire_minutes)
    else:
        expires = now + timedelta(days=settings.refresh_token_expire_days)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": expires,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: TokenType = "access") -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("token is invalid or expired") from exc
    if payload.get("type") != expected_type:
        raise InvalidTokenError(f"expected a {expected_type} token")
    return payload

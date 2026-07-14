"""Pydantic schemas (public API contracts)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.application.services.key_service import is_expiring_soon
from app.core.config import get_settings
from app.domain.enums import KeyCheckResult, Role
from app.infrastructure.db.models import ApiKey


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Auth / users -----------------------------------------------------------


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    role: Role = Role.VIEWER


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    full_name: str | None = None
    role: Role | None = None
    is_active: bool | None = None


class UserOut(ORMModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    role: Role
    is_active: bool
    created_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


# --- API keys ----------------------------------------------------------------


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    api_key: str = Field(min_length=20, max_length=512)
    project_id: uuid.UUID | None = None
    expires_at: datetime | None = None
    validate_remote: bool = False


class KeyRotate(BaseModel):
    api_key: str = Field(min_length=20, max_length=512)
    expires_at: datetime | None = None


class KeyOut(ORMModel):
    id: uuid.UUID
    name: str
    key_hint: str
    status: str
    expires_at: datetime | None
    last_validated_at: datetime | None
    last_used_at: datetime | None
    usage_count: int
    project_id: uuid.UUID | None
    rotated_from_id: uuid.UUID | None
    created_at: datetime
    expiring_soon: bool = False

    @classmethod
    def from_model(cls, key: ApiKey) -> KeyOut:
        schema = cls.model_validate(key)
        schema.expiring_soon = is_expiring_soon(key, get_settings().expiry_warning_days)
        return schema


class KeyValidationOut(BaseModel):
    key: KeyOut
    result: KeyCheckResult
    status_code: int | None
    detail: str | None


class DispensedKey(BaseModel):
    """The only response that ever contains a plaintext key. Never logged."""

    key_id: uuid.UUID
    name: str
    key_hint: str
    project_id: uuid.UUID | None
    api_key: str


# --- Projects ----------------------------------------------------------------


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None


class ProjectOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    owner_id: uuid.UUID
    created_at: datetime


# --- Stats / audit / misc ----------------------------------------------------


class StatsOverview(BaseModel):
    total_keys: int
    keys_by_status: dict[str, int]
    keys_expiring_soon: int
    total_dispenses: int
    total_users: int
    total_projects: int


class UsagePoint(BaseModel):
    date: str
    count: int


class AuditOut(ORMModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_email: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    detail: dict[str, Any] | None
    ip_address: str | None
    created_at: datetime


class HealthOut(BaseModel):
    status: str
    database: str
    version: str
    environment: str

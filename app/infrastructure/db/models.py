"""SQLAlchemy ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.enums import KeyStatus, Role


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.VIEWER.value, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="owner")


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="project")


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=KeyStatus.ACTIVE.value, index=True, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    rotated_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_keys.id"))

    owner: Mapped[User] = relationship(back_populates="api_keys")
    project: Mapped[Project | None] = relationship(back_populates="api_keys")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_email: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("api_keys.id"), index=True, nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(30), default="dispense", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )

"""Domain enumerations."""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    VIEWER = "viewer"


ROLE_HIERARCHY: dict[Role, int] = {Role.VIEWER: 0, Role.MANAGER: 1, Role.ADMIN: 2}


class KeyStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    INVALID = "invalid"


class KeyCheckResult(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNREACHABLE = "unreachable"


class AuditAction(str, Enum):
    USER_REGISTERED = "user.registered"
    USER_LOGIN = "user.login"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"
    KEY_REGISTERED = "key.registered"
    KEY_VALIDATED = "key.validated"
    KEY_ROTATED = "key.rotated"
    KEY_REVOKED = "key.revoked"
    KEY_DELETED = "key.deleted"
    KEY_DISPENSED = "key.dispensed"
    KEY_EXPIRED = "key.expired"
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_DELETED = "project.deleted"

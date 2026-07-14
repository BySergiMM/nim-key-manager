"""FastMCP server exposing the key-manager as Claude connector tools.

Each tool is a thin inbound adapter: it resolves the acting user from the OAuth
identity (``tool_context``), enforces the same role required by the REST endpoint
and delegates to the same application service, so RBAC and the audit trail behave
identically across REST and MCP.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastmcp import FastMCP

from app import __version__
from app.api.schemas import (
    AuditOut,
    DispensedKey,
    KeyOut,
    KeyValidationOut,
    ProjectOut,
    StatsOverview,
    UsagePoint,
    UserOut,
)
from app.application.services.audit_service import AuditService
from app.application.services.key_service import KeyService
from app.application.services.project_service import ProjectService
from app.application.services.stats_service import StatsService
from app.core.config import Settings
from app.domain.enums import KeyStatus, Role
from app.domain.exceptions import ValidationFailedError
from app.infrastructure.nvidia.client import NvidiaKeyValidator
from app.mcp.auth import build_auth_provider
from app.mcp.identity import tool_context

SERVER_INSTRUCTIONS = (
    "Manage the API keys of your own NVIDIA Build/NIM account: list and inspect "
    "keys, request a ready-to-use key (dispense), register new keys, run assisted "
    "rotation, revoke, validate against NVIDIA and manage projects. NVIDIA offers "
    "no public API to create or rotate keys, so registration and rotation are "
    "assisted: you generate the key at build.nvidia.com and provide it here. "
    "Every operation is authenticated, role-checked and audited."
)


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationFailedError(f"{field} must be a valid UUID") from exc


def _parse_dt(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationFailedError(
            f"expires_at must be an ISO-8601 datetime (got {value!r})"
        ) from exc


def build_mcp_server(settings: Settings, base_url: str) -> FastMCP:
    """Construct the FastMCP server, its OAuth provider and all tools."""
    auth = build_auth_provider(settings, base_url)
    mcp: FastMCP = FastMCP(
        name=settings.app_name,
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
        auth=auth,
    )

    # -- identity -----------------------------------------------------------
    @mcp.tool
    async def whoami() -> UserOut:
        """Return the authenticated user this connector acts as (id, e-mail, role)."""
        async with tool_context(Role.VIEWER) as (_session, actor):
            return UserOut.model_validate(actor)

    # -- keys ---------------------------------------------------------------
    @mcp.tool
    async def list_keys(
        project_id: str | None = None, status: str | None = None
    ) -> list[KeyOut]:
        """List API keys, optionally filtered by project id and/or status
        (active, expired, revoked, invalid)."""
        pid = _parse_uuid(project_id, "project_id") if project_id else None
        status_value = KeyStatus(status).value if status else None
        async with tool_context(Role.VIEWER) as (session, _actor):
            keys = await KeyService(session).list_keys(project_id=pid, status=status_value)
            return [KeyOut.from_model(key) for key in keys]

    @mcp.tool
    async def get_key(key_id: str) -> KeyOut:
        """Get a single API key's metadata by id (never returns the secret)."""
        kid = _parse_uuid(key_id, "key_id")
        async with tool_context(Role.VIEWER) as (session, _actor):
            return KeyOut.from_model(await KeyService(session).get(kid))

    @mcp.tool
    async def dispense_key(project_id: str | None = None) -> DispensedKey:
        """Return a ready-to-use API key (least-recently-used active key), globally
        or for a given project. This response contains the plaintext key; usage is
        recorded and audited. Requires the manager role."""
        pid = _parse_uuid(project_id, "project_id") if project_id else None
        async with tool_context(Role.MANAGER) as (session, actor):
            key, plaintext = await KeyService(session).dispense(project_id=pid, actor=actor)
            return DispensedKey(
                key_id=key.id,
                name=key.name,
                key_hint=key.key_hint,
                project_id=key.project_id,
                api_key=plaintext,
            )

    @mcp.tool
    async def register_key(
        name: str,
        api_key: str,
        project_id: str | None = None,
        expires_at: str | None = None,
        validate_remote: bool = False,
    ) -> KeyOut:
        """Register a key you created at build.nvidia.com (must start with 'nvapi-').
        It is encrypted at rest. Set validate_remote=true to verify it against
        NVIDIA before storing. Optionally attach it to a project and an ISO-8601
        expiry. Requires the manager role."""
        pid = _parse_uuid(project_id, "project_id") if project_id else None
        async with tool_context(Role.MANAGER) as (session, actor):
            key = await KeyService(session, NvidiaKeyValidator()).register(
                name=name,
                api_key=api_key,
                owner=actor,
                project_id=pid,
                expires_at=_parse_dt(expires_at),
                validate_remote=validate_remote,
            )
            return KeyOut.from_model(key)

    @mcp.tool
    async def validate_key(key_id: str) -> KeyValidationOut:
        """Validate a stored key against NVIDIA's official read-only endpoint and
        update its status (active/invalid) accordingly. Requires the manager role."""
        kid = _parse_uuid(key_id, "key_id")
        async with tool_context(Role.MANAGER) as (session, actor):
            key, outcome = await KeyService(session, NvidiaKeyValidator()).validate(
                kid, actor=actor
            )
            return KeyValidationOut(
                key=KeyOut.from_model(key),
                result=outcome.result,
                status_code=outcome.status_code,
                detail=outcome.detail,
            )

    @mcp.tool
    async def rotate_key(
        key_id: str, new_api_key: str, expires_at: str | None = None
    ) -> KeyOut:
        """Assisted rotation: register a replacement key you generated at
        build.nvidia.com and atomically revoke the old one (lineage preserved).
        Destructive: the old key is revoked. Requires the manager role."""
        kid = _parse_uuid(key_id, "key_id")
        async with tool_context(Role.MANAGER) as (session, actor):
            replacement = await KeyService(session).rotate(
                kid, new_api_key=new_api_key, actor=actor, expires_at=_parse_dt(expires_at)
            )
            return KeyOut.from_model(replacement)

    @mcp.tool
    async def revoke_key(key_id: str) -> KeyOut:
        """Revoke an API key so it can no longer be dispensed. Destructive but
        reversible only by registering a new key. Requires the manager role."""
        kid = _parse_uuid(key_id, "key_id")
        async with tool_context(Role.MANAGER) as (session, actor):
            return KeyOut.from_model(await KeyService(session).revoke(kid, actor=actor))

    @mcp.tool
    async def delete_key(key_id: str) -> dict[str, str]:
        """Permanently delete an API key and its usage records. Destructive and
        irreversible. Requires the admin role."""
        kid = _parse_uuid(key_id, "key_id")
        async with tool_context(Role.ADMIN) as (session, actor):
            await KeyService(session).delete(kid, actor=actor)
            return {"status": "deleted", "key_id": str(kid)}

    @mcp.tool
    async def check_expirations() -> list[KeyOut]:
        """Scan active keys and mark those past their expiry date as expired.
        Returns the keys that were just expired. Requires the manager role."""
        async with tool_context(Role.MANAGER) as (session, _actor):
            expired = await KeyService(session).check_expirations()
            return [KeyOut.from_model(key) for key in expired]

    # -- projects -----------------------------------------------------------
    @mcp.tool
    async def list_projects() -> list[ProjectOut]:
        """List all projects that group keys by consumer/workload."""
        async with tool_context(Role.VIEWER) as (session, _actor):
            projects = await ProjectService(session).list_projects()
            return [ProjectOut.model_validate(project) for project in projects]

    @mcp.tool
    async def get_project(project_id: str) -> ProjectOut:
        """Get a single project by id."""
        pid = _parse_uuid(project_id, "project_id")
        async with tool_context(Role.VIEWER) as (session, _actor):
            return ProjectOut.model_validate(await ProjectService(session).get(pid))

    @mcp.tool
    async def create_project(name: str, description: str | None = None) -> ProjectOut:
        """Create a project to group API keys. Requires the manager role."""
        async with tool_context(Role.MANAGER) as (session, actor):
            project = await ProjectService(session).create(
                name=name, description=description, owner=actor
            )
            return ProjectOut.model_validate(project)

    @mcp.tool
    async def update_project(
        project_id: str, name: str | None = None, description: str | None = None
    ) -> ProjectOut:
        """Update a project's name and/or description. Requires the manager role."""
        pid = _parse_uuid(project_id, "project_id")
        changes: dict[str, object] = {}
        if name is not None:
            changes["name"] = name
        if description is not None:
            changes["description"] = description
        async with tool_context(Role.MANAGER) as (session, actor):
            project = await ProjectService(session).update(pid, changes, actor=actor)
            return ProjectOut.model_validate(project)

    @mcp.tool
    async def delete_project(project_id: str) -> dict[str, str]:
        """Delete a project (its keys are unassigned, not deleted). Destructive.
        Requires the manager role."""
        pid = _parse_uuid(project_id, "project_id")
        async with tool_context(Role.MANAGER) as (session, actor):
            await ProjectService(session).delete(pid, actor=actor)
            return {"status": "deleted", "project_id": str(pid)}

    @mcp.tool
    async def assign_key_to_project(project_id: str, key_id: str) -> KeyOut:
        """Attach an existing API key to a project. Requires the manager role."""
        pid = _parse_uuid(project_id, "project_id")
        kid = _parse_uuid(key_id, "key_id")
        async with tool_context(Role.MANAGER) as (session, actor):
            key = await ProjectService(session).assign_key(pid, kid, actor=actor)
            return KeyOut.from_model(key)

    # -- stats & audit ------------------------------------------------------
    @mcp.tool
    async def stats_overview() -> StatsOverview:
        """Inventory and usage summary: totals by status, keys expiring soon,
        dispenses, users and projects."""
        async with tool_context(Role.VIEWER) as (session, _actor):
            return StatsOverview.model_validate(await StatsService(session).overview())

    @mcp.tool
    async def usage_stats(days: int = 30) -> list[UsagePoint]:
        """Daily dispense counts over the last N days (default 30)."""
        async with tool_context(Role.VIEWER) as (session, _actor):
            points = await StatsService(session).usage_timeseries(days=days)
            return [UsagePoint.model_validate(point) for point in points]

    @mcp.tool
    async def list_audit(
        limit: int = 100, offset: int = 0, action: str | None = None
    ) -> list[AuditOut]:
        """List immutable audit entries (most recent first), optionally filtered by
        action (e.g. 'key.dispensed'). Requires the admin role."""
        async with tool_context(Role.ADMIN) as (session, _actor):
            entries = await AuditService(session).list_entries(
                limit=limit, offset=offset, action=action
            )
            return [AuditOut.model_validate(entry) for entry in entries]

    return mcp

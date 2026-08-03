"""Programmatic access to the Alembic migrations shipped inside the package.

The migration scripts live at ``app/migrations`` so they travel with the wheel:
an installed copy can bring its own database up to date from any working
directory, with no ``alembic.ini`` and no source checkout.

Every MCP client spawn goes through :func:`upgrade_if_needed`, and importing
Alembic alone costs ~0.5 s, so the already-migrated case is answered with one
query and Alembic is never imported at all. :data:`EXPECTED_HEAD` makes that
possible without reading the migration scripts; a test asserts it matches them,
so it cannot drift.
"""

from __future__ import annotations

from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

#: Latest revision in ``app/migrations/versions``. Bump it in the same commit
#: that adds a revision -- ``test_expected_head_matches_the_scripts`` fails
#: otherwise, so this can never silently disagree with reality.
EXPECTED_HEAD = "0001"


def _config() -> object:
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # env.py reads the URL from Settings; setting it here keeps `alembic
    # current`-style helpers working when they inspect the config directly.
    from app.core.config import get_settings

    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


def upgrade(revision: str = "head") -> None:
    """Apply migrations up to ``revision`` (default: latest)."""
    from alembic import command

    command.upgrade(_config(), revision)  # type: ignore[arg-type]


def downgrade(revision: str) -> None:
    from alembic import command

    command.downgrade(_config(), revision)  # type: ignore[arg-type]


def upgrade_if_needed() -> bool:
    """Migrate only when the database is behind. Returns ``True`` if it ran.

    Any uncertainty -- unreadable version table, unexpected revision, database
    that does not exist yet -- resolves to running the real upgrade, which is
    idempotent. The fast path is only taken on a positive match.
    """
    if stamped_revision() == EXPECTED_HEAD:
        return False
    upgrade()
    return True


def head_revision() -> str | None:
    """Latest revision according to the shipped scripts (imports Alembic)."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_config())  # type: ignore[arg-type]
    return script.get_current_head()


def stamped_revision() -> str | None:
    """Revision recorded in the database, without importing Alembic.

    ``None`` when the table does not exist yet (fresh database), the database is
    unreachable, or the answer is not a single unambiguous revision.
    """
    import asyncio

    from sqlalchemy import text

    from app.infrastructure.db.session import engine

    async def _read() -> str | None:
        async with engine.connect() as connection:
            rows = (await connection.execute(text("SELECT version_num FROM alembic_version"))).all()
        return str(rows[0][0]) if len(rows) == 1 else None

    try:
        return asyncio.run(_read())
    except Exception:  # noqa: BLE001 - missing table, locked file, bad URL, ...
        return None


async def current_revision() -> str | None:
    """Revision currently stamped in the database (``None`` if never migrated)."""
    from alembic.runtime.migration import MigrationContext

    from app.infrastructure.db.session import engine

    def _read(connection: object) -> str | None:
        return MigrationContext.configure(connection).get_current_revision()  # type: ignore[arg-type]

    async with engine.connect() as connection:
        return await connection.run_sync(_read)  # type: ignore[arg-type]

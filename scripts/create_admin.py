#!/usr/bin/env python3
"""Create an administrator account.

Usage:
    python scripts/create_admin.py <email> <password> [full name]

Requires DATABASE_URL and the security env vars to be set.
"""

from __future__ import annotations

import asyncio
import sys

from app.application.services.auth_service import AuthService
from app.domain.enums import Role
from app.infrastructure.db.repositories import UserRepository
from app.infrastructure.db.session import SessionFactory


async def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    email, password = sys.argv[1], sys.argv[2]
    full_name = sys.argv[3] if len(sys.argv) > 3 else None
    async with SessionFactory() as session:
        existing = await UserRepository(session).get_by_email(email)
        if existing is not None:
            existing.role = Role.ADMIN.value
            await session.commit()
            print(f"Promoted existing user {email} to admin.")
            return
        user = await AuthService(session).register(
            email=email, password=password, full_name=full_name, role=Role.ADMIN, actor=None
        )
        print(f"Created admin {user.email} ({user.id}).")


if __name__ == "__main__":
    asyncio.run(main())

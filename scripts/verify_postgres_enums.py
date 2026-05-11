"""Verify postgres agentrole + team enums match foundation/identity.

Run: `uv run python scripts/verify_postgres_enums.py`

Exits 0 on match, 1 on drift (with a diff). If drift, an Alembic
migration must be written before Phase 1 can complete.

Reads DB connection from roboco.config (same env as the orchestrator).
On connection failure (postgres unreachable), prints a clear message
and exits 1 — callers (e.g. `make foundation-check`) handle that as a
"skip" rather than a hard failure.
"""

from __future__ import annotations

import asyncio
import sys

import asyncpg
from roboco.config import settings
from roboco.foundation import identity


async def fetch_enum_values(conn: asyncpg.Connection, enum_name: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT e.enumlabel
        FROM pg_enum e
        JOIN pg_type t ON e.enumtypid = t.oid
        WHERE t.typname = $1
        ORDER BY e.enumsortorder
        """,
        enum_name,
    )
    return {r["enumlabel"] for r in rows}


async def main() -> int:
    try:
        conn = await asyncpg.connect(
            host=settings.database_host,
            port=settings.database_port,
            user=settings.database_user,
            password=settings.database_password,
            database=settings.database_name,
        )
    except (OSError, asyncpg.PostgresError) as exc:
        print(f"postgres unreachable: {exc}")
        print(
            "Run this verifier from an environment with the orchestrator DB available."
        )
        return 1
    try:
        agentrole_db = await fetch_enum_values(conn, "agentrole")
        team_db = await fetch_enum_values(conn, "team")
    finally:
        await conn.close()

    foundation_roles = {r.value for r in identity.Role}
    foundation_teams = {t.value for t in identity.Team}

    drift = False
    missing_roles_in_db = foundation_roles - agentrole_db
    extra_roles_in_db = agentrole_db - foundation_roles
    missing_teams_in_db = foundation_teams - team_db
    extra_teams_in_db = team_db - foundation_teams

    if missing_roles_in_db:
        print(f"DRIFT: postgres agentrole missing: {sorted(missing_roles_in_db)}")
        drift = True
    if extra_roles_in_db:
        print(f"DRIFT: postgres agentrole has extra: {sorted(extra_roles_in_db)}")
        drift = True
    if missing_teams_in_db:
        print(f"DRIFT: postgres team missing: {sorted(missing_teams_in_db)}")
        drift = True
    if extra_teams_in_db:
        print(f"DRIFT: postgres team has extra: {sorted(extra_teams_in_db)}")
        drift = True

    if drift:
        print("Write an Alembic migration to align postgres with foundation.")
        return 1

    print(
        f"OK: agentrole has {len(agentrole_db)} values; team has {len(team_db)} values."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

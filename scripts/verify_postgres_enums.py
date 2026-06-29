"""Verify postgres agentrole + team enums match foundation/identity.

Run: `uv run python scripts/verify_postgres_enums.py`

Exit codes:
  0 — match, OR skip (postgres unreachable / DB not migrated: both enum
      types absent). A skip is not a failure: the gate only fails on real
      drift against a schema that actually has the enum types.
  1 — drift (the enum types exist but their labels differ from foundation).
      An Alembic migration must be written to align postgres with foundation.

Reads DB connection from roboco.config. Skip semantics live IN the script
(exit 0) so the Makefile gate can't mask a real drift exit as "skipped".
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


async def type_exists(conn: asyncpg.Connection, enum_name: str) -> bool:
    """True iff a postgres enum type named ``enum_name`` exists."""
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = $1)", enum_name
        )
    )


def should_skip_for_unmigrated(agentrole_exists: bool, team_exists: bool) -> bool:
    """Skip only when BOTH enum types are absent (DB schema not migrated).

    A partial schema (one type present, the other absent) is itself drift —
    don't skip; let ``enum_drift`` report it. An empty/unmigrated DB (both
    absent) has no migrated target to check, so skipping is correct.
    """
    return not agentrole_exists and not team_exists


def enum_drift(
    db_roles: set[str],
    db_teams: set[str],
    foundation_roles: set[str],
    foundation_teams: set[str],
) -> tuple[bool, list[str]]:
    """Compare DB enum labels to foundation; return (has_drift, messages)."""
    messages: list[str] = []
    missing_roles = foundation_roles - db_roles
    extra_roles = db_roles - foundation_roles
    missing_teams = foundation_teams - db_teams
    extra_teams = db_teams - foundation_teams
    if missing_roles:
        messages.append(f"DRIFT: postgres agentrole missing: {sorted(missing_roles)}")
    if extra_roles:
        messages.append(f"DRIFT: postgres agentrole has extra: {sorted(extra_roles)}")
    if missing_teams:
        messages.append(f"DRIFT: postgres team missing: {sorted(missing_teams)}")
    if extra_teams:
        messages.append(f"DRIFT: postgres team has extra: {sorted(extra_teams)}")
    return (bool(messages), messages)


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
        print("  (skipped — postgres unreachable)")
        return 0
    try:
        agentrole_exists = await type_exists(conn, "agentrole")
        team_exists = await type_exists(conn, "team")
        if should_skip_for_unmigrated(agentrole_exists, team_exists):
            print(
                "  (skipped — DB schema not migrated: agentrole/team enum types absent)"
            )
            return 0
        agentrole_db = await fetch_enum_values(conn, "agentrole")
        team_db = await fetch_enum_values(conn, "team")
    finally:
        await conn.close()

    foundation_roles = {r.value for r in identity.Role}
    foundation_teams = {t.value for t in identity.Team}
    has_drift, messages = enum_drift(
        agentrole_db, team_db, foundation_roles, foundation_teams
    )
    if has_drift:
        for line in messages:
            print(line)
        print("Write an Alembic migration to align postgres with foundation.")
        return 1

    print(
        f"OK: agentrole has {len(agentrole_db)} values; team has {len(team_db)} values."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

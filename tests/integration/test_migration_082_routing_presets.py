"""Migration 082 tests — routing_presets table.

Verifies the DB-level contract the migration establishes: a unique
constraint on `name` (so `save_routing_preset`'s duplicate-name 409 has a
real backstop, not just an app-level pre-check) and a JSONB `payload` column
that round-trips a nested dict/list structure faithfully.

NOT a real alembic round-trip — the suite builds the test DB via
Base.metadata.create_all (see conftest); a real `alembic upgrade head` +
`downgrade -1` round trip against a scratch Postgres (:55432) was run
manually and confirmed clean (create + drop, no errors) as part of building
this migration. See `alembic/versions/082_routing_presets.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from roboco.db.tables import RoutingPresetTable
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_routing_preset_name_is_unique(db_session: AsyncSession) -> None:
    """The `uq_routing_presets_name` constraint rejects a duplicate name at
    the DB level — the backstop behind the service's pre-check 409."""
    db_session.add(RoutingPresetTable(name="dup-name", payload={"assignments": []}))
    await db_session.flush()

    db_session.add(RoutingPresetTable(name="dup-name", payload={"assignments": []}))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_routing_preset_payload_round_trips_nested_structure(
    db_session: AsyncSession,
) -> None:
    """The JSONB payload column stores/returns a nested dict/list structure
    (the shape `save_routing_preset` writes) byte-for-byte."""
    payload = {
        "mode": "mix",
        "assignments": [
            {
                "scope": "agent_slug",
                "scope_value": "be-dev-1",
                "provider_type": "anthropic",
                "model_name": "sonnet",
            },
            {
                "scope": "role",
                "scope_value": "developer:low",
                "provider_type": "anthropic",
                "model_name": "haiku",
            },
        ],
    }
    row = RoutingPresetTable(name="round-trip-preset", payload=payload)
    db_session.add(row)
    await db_session.flush()
    await db_session.refresh(row)

    assert row.payload == payload
    assert row.created_at is not None

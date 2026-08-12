"""A ``board_programs``-scope maintenance pause suppresses origination at
every Board Program entry point: the shared ``_originate_and_record``
chokepoint (cron / run-now / metric-predicate / dogfood release hook) plus
Coroner's and War Room's own EVENT-hook entry points, which bypass it.

DB-backed but writes nothing durable: ``MaintenancePauseService`` only
flushes (never commits), so the ``db_session`` fixture's rollback-on-teardown
cleans up without any explicit purge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.db.tables import SystemSettingTable
from roboco.foundation.policy.maintenance_pause import PauseScope
from roboco.services import board_programs as bp_module
from roboco.services.board_programs import BoardProgramEngine
from roboco.services.coroner_engine import CoronerEngine
from roboco.services.maintenance_pause import get_maintenance_pause_service
from roboco.services.war_room_engine import WarRoomEngine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _pause_board_programs(session: AsyncSession) -> None:
    await get_maintenance_pause_service(session).pause(
        PauseScope.BOARD_PROGRAMS, by="ceo", hours=1
    )


async def _arm(session: AsyncSession, program_key: str) -> None:
    """Arm ``program_key`` via its settings-store override: coroner and
    war_room have no legacy flag alias, so ``program_armed`` reads False by
    default and a not-armed program would no-op BEFORE ever reaching the
    pause check, making the pause test pass for the wrong reason."""
    key = f"board_program.{program_key}.enabled"
    existing = await session.get(SystemSettingTable, key)
    if existing is None:
        session.add(SystemSettingTable(key=key, value="true"))
    else:
        existing.value = "true"
    await session.flush()


@pytest.mark.asyncio
async def test_originate_and_record_no_ops_while_paused(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _pause_board_programs(db_session)
    fake_originator = AsyncMock()
    monkeypatch.setitem(bp_module._ORIGINATORS, "roadmap", fake_originator)

    engine = BoardProgramEngine(db_session)
    result = await engine._originate_and_record("roadmap")

    assert result is None
    fake_originator.assert_not_awaited()


@pytest.mark.asyncio
async def test_originate_and_record_calls_originator_when_not_paused(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity check that the monkeypatch/wiring itself works: without this,
    the "no-ops while paused" test above would pass for the wrong reason
    (a broken fake_originator that's just never reached at all)."""
    fake_originator = AsyncMock(return_value=None)
    monkeypatch.setitem(bp_module._ORIGINATORS, "roadmap", fake_originator)

    engine = BoardProgramEngine(db_session)
    await engine._originate_and_record("roadmap")

    fake_originator.assert_awaited_once()


@pytest.mark.asyncio
async def test_coroner_open_for_incident_no_ops_while_paused(
    db_session: AsyncSession,
) -> None:
    """No incident/agent seeding needed: the pause check short-circuits
    ``open_for_incident`` right after the (now-armed) ``program_armed``
    check, before the incident id is ever resolved."""
    await _arm(db_session, "coroner")
    await _pause_board_programs(db_session)

    result = await CoronerEngine(db_session).open_for_incident(uuid4(), kind="bounced")

    assert result is None


@pytest.mark.asyncio
async def test_war_room_open_for_release_no_ops_while_paused(
    db_session: AsyncSession,
) -> None:
    """No creds/project seeding needed: the pause check short-circuits
    ``_open`` right after the (now-armed) ``program_armed`` check, before the
    X-client/project resolution ``open_for_release`` would otherwise need."""
    await _arm(db_session, "war_room")
    await _pause_board_programs(db_session)

    result = await WarRoomEngine(db_session).open_for_release(
        version="1.2.3", highlights=["a new thing"]
    )

    assert result is None

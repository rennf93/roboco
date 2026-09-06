"""Dispatcher heartbeat — a silently-dead dispatch loop must be detectable.

Live outage (2026-07-01): zero spawns fleet-wide for 4h25m; the old
orchestrator's dispatch loop died with no log line, no audit row, nothing —
the deploy's restart is what fixed it, and the cause is unrecoverable. A
periodic ``dispatcher.alive`` audit row makes "loop dead" distinguishable
from "no work" straight from the DB (and gives the panel a staleness signal).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4  # noqa: F401 - parity with sibling harnesses

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator

_TWO_HEARTBEATS = 2


def _orch(*, paused: bool = False) -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    o = cast("Any", orch)
    o._last_dispatch_heartbeat = None
    o._fire_audit = MagicMock()
    o._is_paused = AsyncMock(return_value=paused)
    return orch


@pytest.mark.asyncio
async def test_first_tick_emits_heartbeat() -> None:
    orch = _orch()
    await orch._emit_dispatcher_heartbeat()
    cast("Any", orch)._fire_audit.assert_called_once()
    kwargs = cast("Any", orch)._fire_audit.call_args.kwargs
    assert kwargs["event_type"] == "dispatcher.alive"


@pytest.mark.asyncio
async def test_heartbeat_throttled_within_window() -> None:
    orch = _orch()
    await orch._emit_dispatcher_heartbeat()
    await orch._emit_dispatcher_heartbeat()
    assert cast("Any", orch)._fire_audit.call_count == 1


@pytest.mark.asyncio
async def test_heartbeat_re_emits_after_window() -> None:
    orch = _orch()
    await orch._emit_dispatcher_heartbeat()
    cast("Any", orch)._last_dispatch_heartbeat = datetime.now(UTC) - timedelta(
        seconds=400
    )
    await orch._emit_dispatcher_heartbeat()
    assert cast("Any", orch)._fire_audit.call_count == _TWO_HEARTBEATS


@pytest.mark.parametrize("paused", [True, False])
@pytest.mark.asyncio
async def test_heartbeat_carries_dispatch_paused_flag(paused: bool) -> None:
    """The uptime ledger needs `dispatch_paused` on every row, matching `_is_paused`."""
    orch = _orch(paused=paused)
    await orch._emit_dispatcher_heartbeat()
    kwargs = cast("Any", orch)._fire_audit.call_args.kwargs
    assert kwargs["details"]["dispatch_paused"] is paused

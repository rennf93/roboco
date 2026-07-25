"""The generic Board Program orchestrator loop — replaces
``_roadmap_engine_loop`` / ``_x_feature_spotlight_loop`` (see
tests/unit/services/test_board_program_engine.py for the engine's own
trigger/dedup/LEARN coverage; this file covers the orchestrator-loop shell
only: interval computation + the sleep/tick/heartbeat wiring).

Unlike the two collapsed loops, ``_board_program_loop`` has no single static
disablement gate — each program's enablement is checked per-tick inside
``BoardProgramEngine``, DB-backed — so there is no "returns immediately when
disabled" behavior to test here; that guarantee now lives in
test_board_program_engine.py's disabled-program coverage.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator

TWO_TICKS = 2
ONE_HOUR_SECONDS = 3600


def _orch() -> Any:
    """Bypass __init__ — the loop helper under test needs only the
    heartbeats dict and ``_running``."""
    o = AgentOrchestrator.__new__(AgentOrchestrator)
    o._loop_heartbeats = {}
    o._running = True
    return o


def test_board_program_interval_is_shortest_registered_cadence_capped() -> None:
    orch = _orch()
    interval = orch._board_program_interval_seconds()
    # x_feature's 1-day default is the shortest of the two registered
    # programs, well above the 300s floor — capped at the 3600s ceiling.
    assert interval == ONE_HOUR_SECONDS


@pytest.mark.asyncio
async def test_board_program_loop_one_tick_exception_does_not_crash_loop() -> None:
    """A raising cycle is logged and the loop keeps ticking — mirrors every
    other engine loop's ``except Exception: logger.exception(...)`` shape."""
    orch = _orch()
    calls = {"n": 0}

    async def _cycle() -> None:
        calls["n"] += 1
        if calls["n"] >= TWO_TICKS:
            orch._running = False
        raise RuntimeError("boom")

    orch._run_board_program_cycle = AsyncMock(side_effect=_cycle)

    with patch("asyncio.sleep", new=AsyncMock()):
        await orch._board_program_loop()

    assert calls["n"] == TWO_TICKS

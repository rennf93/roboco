"""The orchestrator's Entire trails drain loop: dormant unless armed; armed,
it drains every interval and records its heartbeat."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.entire_trails import drain

if TYPE_CHECKING:
    import pytest

_INTERVAL = 7
_PASSES_UNTIL_STOP = 2


def _orch() -> Any:
    o = AgentOrchestrator.__new__(AgentOrchestrator)
    o._loop_heartbeats = {}
    o._running = True
    return o


async def test_the_loop_is_dormant_unless_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _orch()
    monkeypatch.setattr(settings, "entire_trails_enabled", False)
    orch._run_entire_trails_pass = AsyncMock()

    await orch._entire_trails_loop()

    orch._run_entire_trails_pass.assert_not_awaited()
    assert "entire_trails" not in orch._loop_heartbeats


async def test_an_armed_loop_drains_and_records_its_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _orch()
    monkeypatch.setattr(settings, "entire_trails_enabled", True)
    monkeypatch.setattr(settings, "entire_trails_drain_interval_seconds", _INTERVAL)

    async def _stop_after_pass() -> int:
        orch._running = False
        return 3

    drain_due = AsyncMock(side_effect=_stop_after_pass)
    monkeypatch.setattr(drain, "drain_due", drain_due)

    with patch("asyncio.sleep", new=AsyncMock()) as sleep:
        await orch._entire_trails_loop()

    drain_due.assert_awaited_once()
    sleep.assert_awaited_with(_INTERVAL)
    _last_success, interval = orch._loop_heartbeats["entire_trails"]
    assert interval == _INTERVAL


async def test_a_failed_pass_does_not_end_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _orch()
    monkeypatch.setattr(settings, "entire_trails_enabled", True)
    passes = 0

    async def _fail_then_stop() -> int:
        nonlocal passes
        passes += 1
        if passes == 1:
            raise RuntimeError("database went away")
        orch._running = False
        return 0

    monkeypatch.setattr(drain, "drain_due", AsyncMock(side_effect=_fail_then_stop))

    with patch("asyncio.sleep", new=AsyncMock()):
        await orch._entire_trails_loop()

    assert passes == _PASSES_UNTIL_STOP

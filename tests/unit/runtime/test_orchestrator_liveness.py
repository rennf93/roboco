"""Engine-loop liveness watchdog: heartbeat + 2x-interval staleness alert.

Each background engine loop records a monotonic heartbeat after a successful
cycle (and once at start); ``_check_loop_liveness`` (called from
``_check_health``) logs a warning when ``now - last_success > 2 * interval``
for any loop. The alert is the fail-direction: a dead cycle task stops
recording, so after ``2*interval`` the health loop logs "engine loop stalled".
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.runtime import orchestrator as orch_module
from roboco.runtime.orchestrator import AgentOrchestrator

# Test interval constants (kept symbolic so ruff PLR2004 stays quiet and the
# intent reads at the call site).
_CI_WATCH_INTERVAL = 0.01
_VIDEO_RENDER_INTERVAL = 0.05


def _orch() -> Any:
    """Bypass __init__ — the loop helpers under test need only the heartbeats
    dict and ``_running``."""
    o = AgentOrchestrator.__new__(AgentOrchestrator)
    o._loop_heartbeats = {}
    o._running = True
    return o


def test_stale_heartbeat_logs_warning() -> None:
    orch = _orch()
    interval = 10.0
    orch._loop_heartbeats["self_heal"] = (time.monotonic() - 3 * interval, interval)
    fake = MagicMock()
    with patch.object(orch_module, "logger", fake):
        orch._check_loop_liveness()
    fake.warning.assert_called_once()
    args, kwargs = fake.warning.call_args
    assert args[0] == "engine loop stalled past 2x interval"
    assert kwargs["loop"] == "self_heal"
    assert kwargs["interval"] == interval
    assert kwargs["stall_seconds"] >= 3 * interval


def test_fresh_heartbeat_no_warning() -> None:
    orch = _orch()
    interval = 10.0
    orch._loop_heartbeats["self_heal"] = (time.monotonic(), interval)
    fake = MagicMock()
    with patch.object(orch_module, "logger", fake):
        orch._check_loop_liveness()
    fake.warning.assert_not_called()


def test_empty_heartbeats_no_warning() -> None:
    """A fleet with all engines dormant (no heartbeats recorded) must not warn —
    nothing is stalled, nothing is running."""
    orch = _orch()
    fake = MagicMock()
    with patch.object(orch_module, "logger", fake):
        orch._check_loop_liveness()
    fake.warning.assert_not_called()


@pytest.mark.asyncio
async def test_successful_cycle_records_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driving one engine loop (ci_watch) through a stubbed successful cycle
    records a heartbeat under the loop's canonical name."""
    orch = _orch()
    monkeypatch.setattr(settings, "ci_watch_enabled", True)
    monkeypatch.setattr(settings, "ci_watch_interval_seconds", 0.01)

    async def _stop_after_cycle() -> None:
        orch._running = False

    orch._run_ci_watch_cycle = AsyncMock(side_effect=_stop_after_cycle)

    with patch("asyncio.sleep", new=AsyncMock()):
        await orch._ci_watch_loop()

    assert "ci_watch" in orch._loop_heartbeats
    last_success, interval = orch._loop_heartbeats["ci_watch"]
    assert interval == _CI_WATCH_INTERVAL
    assert last_success > 0.0


@pytest.mark.asyncio
async def test_start_heartbeat_recorded_before_first_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The start-of-loop heartbeat is recorded before the first cycle, so a
    loop that never enters its body still has a heartbeat to age against."""
    orch = _orch()
    monkeypatch.setattr(settings, "ci_watch_enabled", True)
    monkeypatch.setattr(settings, "ci_watch_interval_seconds", 0.01)
    # Loop body never runs: while-condition is False on first check.
    orch._running = False
    orch._run_ci_watch_cycle = AsyncMock()

    await orch._ci_watch_loop()

    assert "ci_watch" in orch._loop_heartbeats
    orch._run_ci_watch_cycle.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_cycle_does_not_record_post_success_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cycle that raises must NOT record the post-success heartbeat — the
    staleness alert relies on a dead cycle stopping the heartbeat refresh."""
    orch = _orch()
    monkeypatch.setattr(settings, "ci_watch_enabled", True)
    monkeypatch.setattr(settings, "ci_watch_interval_seconds", 0.01)

    async def _raise_then_stop() -> None:
        orch._running = False
        raise RuntimeError("cycle blew up")

    orch._run_ci_watch_cycle = AsyncMock(side_effect=_raise_then_stop)

    with patch("asyncio.sleep", new=AsyncMock()):
        await orch._ci_watch_loop()

    assert "ci_watch" in orch._loop_heartbeats
    # Only the start heartbeat was recorded; the post-success call was skipped
    # because the cycle raised before reaching it.
    _last_success, interval = orch._loop_heartbeats["ci_watch"]
    assert interval == _CI_WATCH_INTERVAL


@pytest.mark.asyncio
async def test_video_render_loop_records_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity-check that a second engine loop (video_render) uses its own
    canonical name and interval — guards against copy-paste name drift."""
    orch = _orch()
    monkeypatch.setattr(settings, "video_engine_enabled", True)
    monkeypatch.setattr(settings, "video_render_interval_seconds", 0.05)

    async def _stop_after_cycle() -> None:
        orch._running = False

    orch._run_video_render_cycle = AsyncMock(side_effect=_stop_after_cycle)

    with patch("asyncio.sleep", new=AsyncMock()):
        await orch._video_render_loop()

    assert "video_render" in orch._loop_heartbeats
    _, interval = orch._loop_heartbeats["video_render"]
    assert interval == _VIDEO_RENDER_INTERVAL

"""A tripped respawn breaker self-heals after a cooldown.

2026-07-06: pr-reviewer-1 wedged at count=63 because migration 051 made the
PM-respawn counter DB-durable and the breaker had no reset path once tripped
— the only reset was a task status change, which can't happen while the
breaker blocks the spawn. A deploy that fixed the underlying loop (auth/
prompt/schema) couldn't clear the wedge without manual ``DELETE FROM
respawn_tracker`` surgery. The tripped breaker now freezes ``last_check`` at
the trip tick and, after ``pm_respawn_trip_cooldown_seconds``, lets ONE spawn
through. A still-wedged task re-trips after the threshold (bounded re-burn); a
fixed one advances and the status-change path fully resets. Restore re-stamps
``last_check`` to now, so a freshly restored row still trips immediately
(durability preserved) — only a row tripped longer than the cooldown self-heals.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator

_WEDGED_COUNT = 63  # a counter driven far past the trip threshold by a storm


def _new_orchestrator() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._pm_respawn_tracker = {}
    orch._bg_tasks = set()
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    return orch


def _quiet_audit() -> AsyncMock:
    audit = AsyncMock()
    audit.has_recent_tracing_gap = AsyncMock(return_value=False)
    return audit


@contextlib.contextmanager
def _patches() -> Any:
    with (
        patch(
            "roboco.services.audit.get_audit_service",
            return_value=_quiet_audit(),
        ),
        patch(
            "roboco.services.notification.NotificationService",
            return_value=AsyncMock(),
        ),
    ):
        yield


async def _trip(orch: AgentOrchestrator, slug: str, task: dict[str, Any]) -> None:
    """Drive same-status ticks until the breaker trips (count=threshold+1)."""
    for _ in range(orch._PM_RESPAWN_MAX_UNPRODUCTIVE + 1):
        await orch._pm_respawn_should_gate(slug, task)


@pytest.mark.asyncio
async def test_tripped_breaker_self_heals_after_cooldown() -> None:
    """count past threshold + stale last_check -> next tick lets the spawn through."""
    orch = _new_orchestrator()
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}
    with _patches():
        await _trip(orch, "pr-reviewer-1", task)
        assert await orch._pm_respawn_should_gate("pr-reviewer-1", task) is True
        key = ("pr-reviewer-1", task_id)
        assert (
            orch._pm_respawn_tracker[key]["count"] > orch._PM_RESPAWN_MAX_UNPRODUCTIVE
        )

        # The durable row's last_check is stale — the trip happened >cooldown ago
        # (e.g. a deploy fixed the underlying loop and the orchestrator restarted).
        orch._pm_respawn_tracker[key]["last_check"] = datetime.now(UTC) - timedelta(
            seconds=orch._PM_RESPAWN_TRIP_COOLDOWN_SECONDS + 60
        )
        # Cooldown elapsed -> reset count=1, allow the spawn (self-heal).
        assert await orch._pm_respawn_should_gate("pr-reviewer-1", task) is False
        assert orch._pm_respawn_tracker[key]["count"] == 1
        assert orch._pm_respawn_tracker[key]["notified"] is False


@pytest.mark.asyncio
async def test_tripped_breaker_stays_gated_within_cooldown() -> None:
    """A fresh trip keeps gating; the count is frozen, not climbing every tick."""
    orch = _new_orchestrator()
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}
    with _patches():
        await _trip(orch, "be-dev-1", task)
        frozen = orch._pm_respawn_tracker[("be-dev-1", task_id)]["count"]
        # Two more ticks while within the cooldown: both gate, count frozen.
        assert await orch._pm_respawn_should_gate("be-dev-1", task) is True
        assert await orch._pm_respawn_should_gate("be-dev-1", task) is True
        assert orch._pm_respawn_tracker[("be-dev-1", task_id)]["count"] == frozen, (
            "count must freeze once tripped, not climb every dispatch tick"
        )


@pytest.mark.asyncio
async def test_cooldown_retrip_bounds_reburn() -> None:
    """After a cooldown reset, a still-wedged task re-trips only after the
    threshold (bounded re-burn), not immediately."""
    orch = _new_orchestrator()
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}
    with _patches():
        await _trip(orch, "be-dev-1", task)
        key = ("be-dev-1", task_id)
        # Force the cooldown to elapse and consume the reset.
        orch._pm_respawn_tracker[key]["last_check"] = datetime.now(UTC) - timedelta(
            seconds=orch._PM_RESPAWN_TRIP_COOLDOWN_SECONDS + 1
        )
        assert await orch._pm_respawn_should_gate("be-dev-1", task) is False
        # Still wedged (status never changes): re-trip after threshold+1 ticks.
        gated = False
        for _ in range(orch._PM_RESPAWN_MAX_UNPRODUCTIVE + 1):
            if await orch._pm_respawn_should_gate("be-dev-1", task):
                gated = True
                break
        assert gated, "a still-wedged task must re-trip after the threshold"


@pytest.mark.asyncio
async def test_freshly_restored_row_trips_immediately_not_cooldown_reset() -> None:
    """Durability: a restored tripped row with a fresh (re-stamped) last_check
    still gates on the first tick — the cooldown must not disarm a fresh
    restore. (Restore re-stamps last_check to now in production.)"""
    orch = _new_orchestrator()
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}
    # Simulate a restored row: count past threshold, notified, last_check=now.
    orch._pm_respawn_tracker[("be-pm", task_id)] = {
        "count": _WEDGED_COUNT,
        "last_status": "pending",
        "last_check": datetime.now(UTC),
        "tracing_resets": 0,
        "notified": True,
    }
    with _patches():
        gated = await orch._pm_respawn_should_gate("be-pm", task)
    assert gated is True, "a freshly restored row must trip, not cooldown-reset"
    assert orch._pm_respawn_tracker[("be-pm", task_id)]["count"] == _WEDGED_COUNT

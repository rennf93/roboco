"""The respawn breaker must not be fooled by status ping-pong.

Live 2026-07-02: a dev looped blocked -> in_progress -> blocked for two hours
(8 spawns, 30 gateway rejections) and the breaker never tripped — every
status CHANGE fully reset the strike counter, and an A<->B oscillation
changes status on every spawn. A revisited status now gets a bounded reset
budget (mirroring tracing_resets); genuinely new statuses keep the full
reset so forward progress is never punished.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


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


@pytest.mark.asyncio
async def test_status_ping_pong_eventually_trips_the_gate() -> None:
    """blocked <-> in_progress oscillation accrues strikes past the budget."""
    orch = _new_orchestrator()
    task_id = str(uuid4())
    statuses = ["blocked", "in_progress"] * 6
    results = []
    with (
        patch("roboco.services.audit.get_audit_service", return_value=_quiet_audit()),
        patch(
            "roboco.services.notification.NotificationService",
            return_value=AsyncMock(),
        ),
    ):
        for status in statuses:
            results.append(
                await orch._pm_respawn_should_gate(
                    "be-dev-1", {"id": task_id, "status": status}
                )
            )
    assert any(results), (
        "an A<->B status oscillation never accumulated strikes — the exact "
        "2026-07-02 two-hour loop the breaker exists to stop"
    )


@pytest.mark.asyncio
async def test_forward_progress_through_new_statuses_never_gates() -> None:
    orch = _new_orchestrator()
    task_id = str(uuid4())
    lifecycle = ["pending", "claimed", "in_progress", "verifying", "awaiting_qa"]
    with (
        patch("roboco.services.audit.get_audit_service", return_value=_quiet_audit()),
        patch(
            "roboco.services.notification.NotificationService",
            return_value=AsyncMock(),
        ),
    ):
        for status in lifecycle:
            assert not await orch._pm_respawn_should_gate(
                "be-dev-1", {"id": task_id, "status": status}
            ), f"forward progress into {status} must not gate"


@pytest.mark.asyncio
async def test_single_revisit_within_budget_does_not_gate() -> None:
    """A legitimate revision cycle (one revisit) stays under the budget."""
    orch = _new_orchestrator()
    task_id = str(uuid4())
    with (
        patch("roboco.services.audit.get_audit_service", return_value=_quiet_audit()),
        patch(
            "roboco.services.notification.NotificationService",
            return_value=AsyncMock(),
        ),
    ):
        for status in ["in_progress", "awaiting_qa", "in_progress", "awaiting_qa"]:
            assert not await orch._pm_respawn_should_gate(
                "be-dev-1", {"id": task_id, "status": status}
            ), "one revision round-trip must not trip the breaker"

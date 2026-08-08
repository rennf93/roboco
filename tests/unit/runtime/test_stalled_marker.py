"""Durable stalled-marker on the breaker path (task 45c3c148).

``_pm_respawn_should_gate`` now records a durable stalled/needs-human marker
on the task row itself (``TaskService.mark_stalled``) at the exact point it
fires the one-shot CEO notification (``_notify_stuck_agent``), and clears it
(``TaskService.clear_stalled_marker``) via the SAME genuine-forward-progress
branch that resets the strike counter (``_respawn_status_change_resets``).
Both the marker and the notification stay one-shot per trip, mirroring the
existing ``record["notified"]`` discipline — a re-trip after the cooldown
window self-heal attempt is a fresh trip and gets a fresh one-shot mark +
notify, but ticks that stay gated within one trip must not double either.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from roboco.models.base import StalledReason
from roboco.runtime.orchestrator import AgentOrchestrator


def _new_orchestrator() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._bg_tasks = set()
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    return orch


def _quiet_audit() -> AsyncMock:
    audit = AsyncMock()
    audit.has_recent_tracing_gap = AsyncMock(return_value=False)
    return audit


@contextlib.asynccontextmanager
async def _fake_db_context() -> Any:
    yield MagicMock()


async def _drain_bg(orch: AgentOrchestrator) -> None:
    """Await every fire-and-forget background task the gate scheduled."""
    tasks = list(cast("Any", orch)._bg_tasks)
    if tasks:
        await asyncio.gather(*tasks)


async def _trip(orch: AgentOrchestrator, slug: str, task: dict[str, Any]) -> None:
    for _ in range(orch._PM_RESPAWN_MAX_UNPRODUCTIVE + 1):
        await orch._pm_respawn_should_gate(slug, task)


@pytest.mark.asyncio
async def test_breaker_trip_records_durable_stalled_marker() -> None:
    """A tripped breaker sets the durable marker via TaskService.mark_stalled."""
    orch = _new_orchestrator()
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}

    task_service = AsyncMock()
    task_service.mark_stalled = AsyncMock()

    with (
        patch("roboco.services.audit.get_audit_service", return_value=_quiet_audit()),
        patch(
            "roboco.services.notification.NotificationService",
            return_value=AsyncMock(),
        ),
        patch("roboco.db.base.get_db_context", _fake_db_context),
        patch("roboco.services.task.TaskService", return_value=task_service),
    ):
        await _trip(orch, "be-pm", task)

    task_service.mark_stalled.assert_awaited_once_with(
        UUID(task_id), reason=StalledReason.BREAKER_TRIPPED.value
    )


@pytest.mark.asyncio
async def test_genuine_progress_clears_stalled_marker() -> None:
    """A REAL status change (a status never seen before) clears the marker."""
    orch = _new_orchestrator()
    task_id = str(uuid4())

    task_service = AsyncMock()
    task_service.clear_stalled_marker = AsyncMock()

    with (
        patch("roboco.services.audit.get_audit_service", return_value=_quiet_audit()),
        patch("roboco.db.base.get_db_context", _fake_db_context),
        patch("roboco.services.task.TaskService", return_value=task_service),
    ):
        # First tick just seeds the tracker (no prior status to compare).
        await orch._pm_respawn_should_gate(
            "be-pm", {"id": task_id, "status": "pending"}
        )
        task_service.clear_stalled_marker.assert_not_awaited()
        # Genuinely new status -> forward progress -> clear scheduled + drained.
        await orch._pm_respawn_should_gate(
            "be-pm", {"id": task_id, "status": "in_progress"}
        )
        await _drain_bg(orch)

    task_service.clear_stalled_marker.assert_awaited_once_with(UUID(task_id))


@pytest.mark.asyncio
async def test_revisited_status_does_not_clear_stalled_marker() -> None:
    """A REVISITED status (ping-pong) is not genuine progress — no clear call."""
    orch = _new_orchestrator()
    task_id = str(uuid4())

    task_service = AsyncMock()
    task_service.clear_stalled_marker = AsyncMock()

    with (
        patch("roboco.services.audit.get_audit_service", return_value=_quiet_audit()),
        patch("roboco.db.base.get_db_context", _fake_db_context),
        patch("roboco.services.task.TaskService", return_value=task_service),
    ):
        await orch._pm_respawn_should_gate(
            "be-pm", {"id": task_id, "status": "blocked"}
        )
        await orch._pm_respawn_should_gate(
            "be-pm", {"id": task_id, "status": "in_progress"}
        )
        await _drain_bg(orch)
        task_service.clear_stalled_marker.reset_mock()
        # blocked was already seen -> a revisit, not genuine forward progress.
        await orch._pm_respawn_should_gate(
            "be-pm", {"id": task_id, "status": "blocked"}
        )
        await _drain_bg(orch)

    task_service.clear_stalled_marker.assert_not_awaited()


@pytest.mark.asyncio
async def test_retrip_after_cooldown_is_one_shot_not_double_counted() -> None:
    """Marker + notification stay one-shot per trip across a cooldown re-trip."""
    orch = _new_orchestrator()
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}

    task_service = AsyncMock()
    task_service.mark_stalled = AsyncMock()
    notifier = AsyncMock()
    notifier.send_stuck_agent_notification = AsyncMock()

    with (
        patch("roboco.services.audit.get_audit_service", return_value=_quiet_audit()),
        patch(
            "roboco.services.notification.NotificationService",
            return_value=notifier,
        ),
        patch("roboco.db.base.get_db_context", _fake_db_context),
        patch("roboco.services.task.TaskService", return_value=task_service),
    ):
        await _trip(orch, "be-dev-1", task)
        assert task_service.mark_stalled.await_count == 1
        assert notifier.send_stuck_agent_notification.await_count == 1

        # Still gated within the cooldown window — must NOT re-mark/re-notify.
        assert await orch._pm_respawn_should_gate("be-dev-1", task) is True
        assert await orch._pm_respawn_should_gate("be-dev-1", task) is True
        assert task_service.mark_stalled.await_count == 1
        assert notifier.send_stuck_agent_notification.await_count == 1

        # Force the cooldown to elapse — still wedged (status never advances).
        key = ("be-dev-1", task_id)
        orch._pm_respawn_tracker[key]["last_check"] = datetime.now(UTC) - timedelta(
            seconds=orch._PM_RESPAWN_TRIP_COOLDOWN_SECONDS + 1
        )
        # Self-heal tick: cooldown elapsed, one spawn let through, not gated.
        assert await orch._pm_respawn_should_gate("be-dev-1", task) is False
        assert task_service.mark_stalled.await_count == 1
        assert notifier.send_stuck_agent_notification.await_count == 1

        # Still wedged: re-trip after the threshold is a FRESH trip.
        gated = False
        for _ in range(orch._PM_RESPAWN_MAX_UNPRODUCTIVE + 1):
            if await orch._pm_respawn_should_gate("be-dev-1", task):
                gated = True
                break
        assert gated, "a still-wedged task must re-trip after the threshold"

    fresh_trip_call_count = 2  # first trip + one re-trip after cooldown self-heal
    assert task_service.mark_stalled.await_count == fresh_trip_call_count
    assert notifier.send_stuck_agent_notification.await_count == fresh_trip_call_count

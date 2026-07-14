"""Scheduled audit trigger in _dispatch_audit_work.

Covers the interval cooldown (ROBOCO_AUDIT_INTERVAL_SECONDS) and the
active-agent breaker that prevents auditor spawn storms.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import Settings, settings
from roboco.runtime.orchestrator import AgentOrchestrator


@pytest.fixture
def orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._notification_spawn_at = {}
    orch._last_audit_spawn_at = None
    return orch


async def _run_dispatch(
    orch: AgentOrchestrator, *, tasks: list[dict] | None = None
) -> MagicMock:
    """Run _dispatch_audit_work with no unobserved alerts and return the spawn mock."""
    client = MagicMock()
    with (
        patch.object(
            orch, "_next_unobserved_audit_alert", new=AsyncMock(return_value=None)
        ),
        patch.object(orch, "_ack_alert_as_auditor", new=AsyncMock()),
        patch.object(orch, "_fetch_tasks", new=AsyncMock(return_value=tasks or [])),
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn_mock,
    ):
        await orch._dispatch_audit_work(client)
    return spawn_mock


@pytest.mark.anyio
async def test_spawns_when_overdue_and_delivery_activity(
    orch: AgentOrchestrator,
) -> None:
    """Interval elapsed + recent tasks -> auditor sweep is spawned."""
    now = datetime.now(UTC)
    with (
        patch.object(settings, "audit_interval_seconds", 21600),
        patch("roboco.runtime.orchestrator.datetime", wraps=datetime) as dt_mock,
    ):
        dt_mock.now.return_value = now
        orch._last_audit_spawn_at = now - timedelta(seconds=21601)
        spawn_mock = await _run_dispatch(
            orch,
            tasks=[
                {
                    "id": "t1",
                    "status": "in_progress",
                    "updated_at": "2026-07-13T00:00:00Z",
                }
            ],
        )
    assert spawn_mock.await_count == 1
    call_kwargs = spawn_mock.await_args.kwargs if spawn_mock.await_args else {}
    assert call_kwargs.get("agent_id") == "auditor"
    assert call_kwargs.get("spawned_by") == "_dispatch_audit_work"


@pytest.mark.anyio
async def test_skips_when_interval_not_elapsed(orch: AgentOrchestrator) -> None:
    """Cooldown: last spawn was recent -> no scheduled sweep."""
    now = datetime.now(UTC)
    with (
        patch.object(settings, "audit_interval_seconds", 21600),
        patch("roboco.runtime.orchestrator.datetime", wraps=datetime) as dt_mock,
    ):
        dt_mock.now.return_value = now
        orch._last_audit_spawn_at = now - timedelta(seconds=1800)
        spawn_mock = await _run_dispatch(
            orch,
            tasks=[
                {
                    "id": "t1",
                    "status": "in_progress",
                    "updated_at": "2026-07-13T00:00:00Z",
                }
            ],
        )
    assert spawn_mock.await_count == 0


@pytest.mark.anyio
async def test_skips_when_no_delivery_activity(orch: AgentOrchestrator) -> None:
    """No active tasks and no recently completed tasks -> no sweep."""
    now = datetime.now(UTC)
    with (
        patch.object(settings, "audit_interval_seconds", 21600),
        patch("roboco.runtime.orchestrator.datetime", wraps=datetime) as dt_mock,
    ):
        dt_mock.now.return_value = now
        orch._last_audit_spawn_at = now - timedelta(seconds=21601)
        spawn_mock = await _run_dispatch(orch, tasks=[])
    assert spawn_mock.await_count == 0


@pytest.mark.anyio
async def test_breaker_skips_when_auditor_active(orch: AgentOrchestrator) -> None:
    """Active-agent breaker prevents a second auditor container."""
    client = MagicMock()
    now = datetime.now(UTC)
    with (
        patch.object(settings, "audit_interval_seconds", 21600),
        patch("roboco.runtime.orchestrator.datetime", wraps=datetime) as dt_mock,
        patch.object(
            orch, "_next_unobserved_audit_alert", new=AsyncMock(return_value=None)
        ),
        patch.object(orch, "_ack_alert_as_auditor", new=AsyncMock()),
        patch.object(orch, "_fetch_tasks", new=AsyncMock(return_value=[])),
        patch.object(orch, "_is_agent_active", return_value=True),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn_mock,
    ):
        dt_mock.now.return_value = now
        orch._last_audit_spawn_at = now - timedelta(seconds=21601)
        await orch._dispatch_audit_work(client)
    assert spawn_mock.await_count == 0


@pytest.mark.anyio
async def test_reactive_alert_stamps_last_spawn_and_blocks_scheduled(
    orch: AgentOrchestrator,
) -> None:
    """A reactive alert spawn records _last_audit_spawn_at and returns early."""
    client = MagicMock()
    alert = {
        "id": "11111111-1111-1111-1111-111111111111",
        "subject": "Coverage gap",
        "body": "Test",
    }
    now = datetime.now(UTC)
    with (
        patch.object(settings, "audit_interval_seconds", 21600),
        patch("roboco.runtime.orchestrator.datetime", wraps=datetime) as dt_mock,
        patch.object(
            orch, "_next_unobserved_audit_alert", new=AsyncMock(return_value=alert)
        ),
        patch.object(orch, "_ack_alert_as_auditor", new=AsyncMock()),
        patch.object(orch, "_fetch_tasks", new=AsyncMock(return_value=[])),
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn_mock,
    ):
        dt_mock.now.return_value = now
        await orch._dispatch_audit_work(client)
    assert spawn_mock.await_count == 1
    assert orch._last_audit_spawn_at == now


@pytest.mark.anyio
async def test_reactive_alert_acks_so_it_cannot_rotate(
    orch: AgentOrchestrator,
) -> None:
    """The alert is acked as the auditor on dispatch, so the next tick —
    which would otherwise re-fetch the same stale alert and respawn — finds
    no unobserved alert and falls through to the scheduled path (blocked by
    the just-stamped _last_audit_spawn_at). This is the rotation-stopper.
    """
    client = MagicMock()
    alert_id = "22222222-2222-2222-2222-222222222222"
    alert = {
        "id": alert_id,
        "subject": "Rework alert",
        "body": "Task entered needs_revision",
    }
    now = datetime.now(UTC)
    ack_mock = AsyncMock()
    fetch_mock = AsyncMock(side_effect=[alert, None])
    with (
        patch.object(settings, "audit_interval_seconds", 21600),
        patch("roboco.runtime.orchestrator.datetime", wraps=datetime) as dt_mock,
        patch.object(orch, "_next_unobserved_audit_alert", new=fetch_mock),
        patch.object(orch, "_ack_alert_as_auditor", new=ack_mock),
        patch.object(orch, "_fetch_tasks", new=AsyncMock(return_value=[])),
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn_mock,
    ):
        dt_mock.now.return_value = now
        await orch._dispatch_audit_work(client)  # tick 1: alert -> spawn + ack
        await orch._dispatch_audit_work(client)  # tick 2: no alert -> scheduled
    assert spawn_mock.await_count == 1  # not respawned on tick 2
    assert ack_mock.await_count == 1
    # acked as the auditor saw it: (client, alert_id)
    assert ack_mock.await_args.args[1] == alert_id


@pytest.mark.anyio
async def test_cooldown_zero_disables_scheduled_sweeps(orch: AgentOrchestrator) -> None:
    """ROBOCO_AUDIT_INTERVAL_SECONDS=0 disables scheduled sweeps entirely."""
    now = datetime.now(UTC)
    with (
        patch.object(settings, "audit_interval_seconds", 0),
        patch("roboco.runtime.orchestrator.datetime", wraps=datetime) as dt_mock,
    ):
        dt_mock.now.return_value = now
        orch._last_audit_spawn_at = None
        spawn_mock = await _run_dispatch(
            orch,
            tasks=[
                {
                    "id": "t1",
                    "status": "in_progress",
                    "updated_at": "2026-07-13T00:00:00Z",
                }
            ],
        )
    assert spawn_mock.await_count == 0


def test_env_override_zero_is_accepted() -> None:
    """ROBOCO_AUDIT_INTERVAL_SECONDS=0 is valid Pydantic input, matching the
    documented disable sentinel and the runtime gate in _audit_spawn_cooled().
    """
    with patch.dict(os.environ, {"ROBOCO_AUDIT_INTERVAL_SECONDS": "0"}, clear=False):
        s = Settings()
    assert s.audit_interval_seconds == 0

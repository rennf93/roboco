"""`_check_sla_for_task` / `_time_in_state`: per-role SLA breach detection must
read active time (fleet downtime discounted via `_active_age`), not raw
wall-clock elapsed, or a CEO-ordered pause reads as SLA breach the instant
the fleet wakes up. No natural existing test file covers this pair (see
tests/unit/runtime/test_stale_claim_reaper.py, test_orchestrator_stuck_tasks.py,
and test_notification_live_work.py for the reaper/stuck-task/notification
siblings of this same fix).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import roboco.services.release_proposal as release_proposal_module
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services import uptime


class _FakeLedger:
    """Reports a fixed `active_elapsed` for any start/end - a stand-in for a
    real `UptimeLedger` carrying a known downtime window."""

    def __init__(self, active_elapsed: timedelta) -> None:
        self._active_elapsed = active_elapsed

    def active_elapsed(self, start: datetime, end: datetime | None = None) -> timedelta:
        del start, end
        return self._active_elapsed


def _in_progress_dev_task(updated_at: datetime) -> dict[str, Any]:
    """A developer task in_progress since `updated_at` - the default
    `agent_sla_developer_in_progress` SLA is 2 hours."""
    return {
        "id": "t1",
        "assigned_to": "be-dev-1-uuid",
        "updated_at": updated_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_sla_still_escalated_without_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task in_progress for 5 wall-clock hours (> the 2h developer SLA)
    with no ledger loaded still escalates - today's behaviour preserved."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_resolve_agent_slug", lambda _x: "be-dev-1")
    escalate = AsyncMock()
    monkeypatch.setattr(orch, "_escalate_sla_breach", escalate)
    task = _in_progress_dev_task(datetime.now(UTC) - timedelta(hours=5))

    await orch._check_sla_for_task(MagicMock(), task, "in_progress")

    escalate.assert_awaited_once()


@pytest.mark.asyncio
async def test_sla_not_escalated_when_stale_by_wall_clock_but_active_time_under_sla(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same 5-wall-clock-hour-old task reads as only 1 hour old in
    active time (a long CEO pause in between) - under the 2h developer SLA,
    so it must NOT escalate even though wall-clock time would trip it."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._uptime = cast("Any", _FakeLedger(timedelta(hours=1)))
    monkeypatch.setattr(orch, "_resolve_agent_slug", lambda _x: "be-dev-1")
    escalate = AsyncMock()
    monkeypatch.setattr(orch, "_escalate_sla_breach", escalate)
    task = _in_progress_dev_task(datetime.now(UTC) - timedelta(hours=5))

    await orch._check_sla_for_task(MagicMock(), task, "in_progress")

    escalate.assert_not_awaited()


# ---------------------------------------------------------------------------
# start() / _mark_running_and_beat: a broken audit write must never look
# like the fleet went down. See tests/unit/services/test_uptime_ledger.py
# for the ledger-side clip_running coverage this seam feeds.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_running_and_beat_marks_process_and_emits_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam `start()` calls: mark the process running, then fire a boot
    heartbeat that closes any pre-boot outage window at the boot instant."""
    uptime._reset_process_marker()
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    beat = AsyncMock()
    monkeypatch.setattr(orch, "_emit_dispatcher_heartbeat", beat)

    await orch._mark_running_and_beat()

    assert uptime.process_running_since() is not None
    beat.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_calls_mark_running_and_beat_before_other_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`start()` is too heavy to drive end-to-end in a unit test (docker,
    redis, ~18 background loops); every other call it makes is stubbed here
    so the one thing under test - that the running-since marker is set
    before anything else can fail partway through boot - is unambiguous."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch.dispatcher_interval = 30
    call_order: list[str] = []

    def _record(name: str) -> AsyncMock:
        return AsyncMock(side_effect=lambda *_args, **_kwargs: call_order.append(name))

    monkeypatch.setattr(orch, "_mark_running_and_beat", _record("mark"))
    for name in (
        "_ensure_agent_image",
        "restore_waiting_records",
        "restore_respawn_tracker",
        "_reconcile_orphan_claims_on_startup",
        "_heal_stale_agent_tokens",
        "_readopt_running_agents",
        "_reconcile_orphan_spawn_sessions",
        "_sandbox_janitor_sweep",
    ):
        monkeypatch.setattr(orch, name, _record(name))
    monkeypatch.setattr(
        release_proposal_module, "sweep_orphan_release_locks", _record("release_locks")
    )

    def _fake_create_task(coro: Any) -> MagicMock:
        coro.close()  # never actually scheduled - these are the ~18 bg loops
        return MagicMock()

    monkeypatch.setattr("asyncio.create_task", _fake_create_task)

    await orch.start()

    assert call_order[0] == "mark"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

"""PM respawn loop guard counts no-progress only, not rule-following retries.

Background
----------
``_pm_respawn_should_gate`` is the orchestrator's circuit breaker against
respawning the same PM on the same task forever. Before Task 13 it
counted any spawn whose task status didn't advance as a "strike", and
killed respawns after three of them.

That works for an agent that's hung — but with the gateway claim-time
gates installed in Phase 3, a rule-following PM that hits
``PARENT_NOT_CLAIMED`` (a ``tracing_gap`` envelope) and is told by the
prompt to call ``i_will_plan`` first will keep returning to the same
status. Each retry would trip a strike even though the agent did
exactly what the gateway told it to do.

The fix: when audit_log shows the agent emitted a ``gateway.rejected``
envelope with ``reason == "tracing_gap"`` since the last spawn, treat
that as forward progress (rule-following) and reset the strike count
instead of incrementing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.seeds.initial_data import AGENT_UUIDS


def _new_orchestrator() -> AgentOrchestrator:
    """Bypass __init__ so tests don't need a full DI graph."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._pm_respawn_tracker = {}  # type: ignore[attr-defined]
    return orch


@pytest.mark.asyncio
async def test_three_tracing_gap_responses_do_not_trip_kill() -> None:
    """A PM rule-followed three times must NOT be killed.

    Each spawn the audit_log shows a fresh ``gateway.rejected`` row with
    ``reason == "tracing_gap"`` since the last check. That's the
    rule-following retry pattern — counter must reset every time.
    """
    orch = _new_orchestrator()
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}

    fake_audit = AsyncMock()
    fake_audit.has_recent_tracing_gap = AsyncMock(return_value=True)

    spawn_attempts = 5
    with patch("roboco.services.audit.get_audit_service", return_value=fake_audit):
        for _ in range(spawn_attempts):
            should_gate = await orch._pm_respawn_should_gate("be-pm", task)
            assert should_gate is False

    # Audit was consulted on every call past the first record-creation.
    expected_audit_calls = spawn_attempts - 1
    assert fake_audit.has_recent_tracing_gap.await_count >= expected_audit_calls


@pytest.mark.asyncio
async def test_three_no_progress_spawns_still_trip_kill() -> None:
    """When there is NO tracing_gap envelope, the strike logic still bites.

    The classic stuck-loop case must still be detected: agent silently
    hung, no envelopes emitted, status doesn't change. Strike count
    increments each spawn and the kill fires after the threshold.
    """
    orch = _new_orchestrator()
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}

    fake_audit = AsyncMock()
    fake_audit.has_recent_tracing_gap = AsyncMock(return_value=False)

    with patch("roboco.services.audit.get_audit_service", return_value=fake_audit):
        # Spawns 1, 2, 3 — under threshold, all allowed.
        for _ in range(3):
            assert await orch._pm_respawn_should_gate("be-pm", task) is False
        # Spawn 4 — count now exceeds _PM_RESPAWN_MAX_UNPRODUCTIVE = 3.
        assert await orch._pm_respawn_should_gate("be-pm", task) is True


@pytest.mark.asyncio
async def test_status_change_resets_strike_count() -> None:
    """Pre-existing reset path on real status change must keep working."""
    orch = _new_orchestrator()
    task_id = str(uuid4())

    fake_audit = AsyncMock()
    fake_audit.has_recent_tracing_gap = AsyncMock(return_value=False)

    with patch("roboco.services.audit.get_audit_service", return_value=fake_audit):
        # Two strikes on pending.
        await orch._pm_respawn_should_gate(
            "be-pm", {"id": task_id, "status": "pending"}
        )
        await orch._pm_respawn_should_gate(
            "be-pm", {"id": task_id, "status": "pending"}
        )
        # Status advances — counter must drop back to 1.
        await orch._pm_respawn_should_gate(
            "be-pm", {"id": task_id, "status": "in_progress"}
        )
        record = orch._pm_respawn_tracker[("be-pm", task_id)]
        assert record["count"] == 1
        assert record["last_status"] == "in_progress"


@pytest.mark.asyncio
async def test_audit_query_uses_correct_agent_uuid_and_task_id() -> None:
    """The audit query must scope to (agent UUID, task UUID, since)."""
    orch = _new_orchestrator()
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}

    fake_audit = AsyncMock()
    fake_audit.has_recent_tracing_gap = AsyncMock(return_value=True)

    with patch("roboco.services.audit.get_audit_service", return_value=fake_audit):
        # First call seeds the record (no audit query yet).
        await orch._pm_respawn_should_gate("be-pm", task)
        # Second call should consult audit with be-pm's UUID + task UUID + since.
        await orch._pm_respawn_should_gate("be-pm", task)

    fake_audit.has_recent_tracing_gap.assert_awaited()
    kwargs = fake_audit.has_recent_tracing_gap.call_args.kwargs
    assert str(kwargs["agent_id"]) == AGENT_UUIDS["be-pm"]
    assert str(kwargs["task_id"]) == task_id
    assert isinstance(kwargs["since"], datetime)
    assert kwargs["since"].tzinfo is UTC


@pytest.mark.asyncio
async def test_unknown_slug_falls_back_to_status_only() -> None:
    """A slug not in AGENT_UUIDS must NOT crash; just skip the audit query.

    Defensive: if the slug map drifts, the kill loop guard still works,
    only it won't get the tracing_gap reset.
    """
    orch = _new_orchestrator()
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}

    fake_audit = AsyncMock()
    fake_audit.has_recent_tracing_gap = AsyncMock(return_value=False)

    with patch("roboco.services.audit.get_audit_service", return_value=fake_audit):
        # Slug not present in AGENT_UUIDS — should not raise.
        for _ in range(3):
            assert await orch._pm_respawn_should_gate("not-a-real-slug", task) is False
        # Threshold trip path still reachable.
        assert await orch._pm_respawn_should_gate("not-a-real-slug", task) is True
    # Audit is never consulted because slug didn't resolve.
    fake_audit.has_recent_tracing_gap.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_query_failure_does_not_crash_gate() -> None:
    """If audit lookup raises, fall back to the legacy strike behavior.

    Audit is observability — it must never block orchestrator decisions.
    """
    orch = _new_orchestrator()
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}

    fake_audit = AsyncMock()
    fake_audit.has_recent_tracing_gap = AsyncMock(side_effect=RuntimeError("db down"))

    with patch("roboco.services.audit.get_audit_service", return_value=fake_audit):
        # Strikes 1-3: allowed. 4th: gated. Same as the no-tracing-gap case.
        for _ in range(3):
            assert await orch._pm_respawn_should_gate("be-pm", task) is False
        assert await orch._pm_respawn_should_gate("be-pm", task) is True

"""Reaper releases a `claimed` task parked behind an agent busy elsewhere.

Live production, 2026-09-05: be-dev-1 ended up holding FOUR `claimed` tasks
(7daca5ff claimed 21:17, 5d95b385 21:41, c15af9ff 22:07, c0d0f4f4 22:07)
whose `last_heartbeat_at == claimed_at` for up to an hour, while its
container worked on something else and be-dev-2 idled. The stale-claim
reaper's live-container skip (`_should_skip_live_reap`) only checks whether
the agent has ANY live container - not whether that container is working
THIS task - so it spared every one of these parked claims forever.

`_maybe_release_parked_claim` closes the gap: a `claimed` row whose
heartbeat never advanced past its own claim, stale past
`stale_claim_reap_seconds` of fleet-active time, is released when the same
agent provably has another task with a fresher heartbeat or an in_progress
task. It never fires on a row whose heartbeat is genuinely advancing, on an
agent's only claim (left to the existing dead-run logic), or while the
claiming agent has an unexpired `_claims_in_flight` entry (a claim still
being finalized).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.runtime.orchestrator import AgentOrchestrator


def _task(task_id: UUID | None = None, **over: Any) -> Any:
    base: dict[str, Any] = {
        "id": task_id or uuid4(),
        "status": TaskStatus.CLAIMED,
        "assigned_to": "be-dev-1",
        "claimed_by": None,
        "claimed_at": None,
        "last_heartbeat_at": None,
    }
    base.update(over)
    return type("T", (), base)()


def _orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._claim_heartbeat_ttl = 300
    return orch


# ---------------------------------------------------------------------------
# _agent_busy_elsewhere
# ---------------------------------------------------------------------------


def test_busy_elsewhere_true_for_in_progress_sibling() -> None:
    orch = _orch()
    this_task = _task(last_heartbeat_at=None)
    sibling = _task(status=TaskStatus.IN_PROGRESS, last_heartbeat_at=None)

    assert orch._agent_busy_elsewhere("be-dev-1", this_task, [this_task, sibling])


def test_busy_elsewhere_true_for_fresher_heartbeat_sibling() -> None:
    orch = _orch()
    now = datetime.now(UTC)
    this_task = _task(last_heartbeat_at=now - timedelta(seconds=1200))
    sibling = _task(last_heartbeat_at=now - timedelta(seconds=30))

    assert orch._agent_busy_elsewhere("be-dev-1", this_task, [this_task, sibling])


def test_busy_elsewhere_false_with_no_sibling() -> None:
    orch = _orch()
    this_task = _task()

    assert not orch._agent_busy_elsewhere("be-dev-1", this_task, [this_task])


def test_busy_elsewhere_false_for_different_agents_sibling() -> None:
    orch = _orch()
    this_task = _task()
    other_agent_task = _task(assigned_to="be-dev-2", status=TaskStatus.IN_PROGRESS)

    assert not orch._agent_busy_elsewhere(
        "be-dev-1", this_task, [this_task, other_agent_task]
    )


# ---------------------------------------------------------------------------
# _maybe_release_parked_claim - (a)/(b)/(c)/(d)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parked_claim_released_when_busy_elsewhere_past_window() -> None:
    """(a) Past the reap window, agent provably busy elsewhere -> released."""
    orch = _orch()
    now = datetime.now(UTC)
    claimed_at = now - timedelta(seconds=1200)
    parked = _task(claimed_at=claimed_at, last_heartbeat_at=claimed_at)
    sibling = _task(status=TaskStatus.IN_PROGRESS, last_heartbeat_at=now)
    svc = AsyncMock()
    svc.unclaim_for_reaper = AsyncMock()

    released = await orch._maybe_release_parked_claim(svc, parked, [parked, sibling])

    assert released is True
    svc.unclaim_for_reaper.assert_awaited_once_with(parked.id)


@pytest.mark.asyncio
async def test_parked_claim_kept_within_window() -> None:
    """(b) Same shape, but claimed only 60s ago (under the 300s TTL) -> kept."""
    orch = _orch()
    now = datetime.now(UTC)
    claimed_at = now - timedelta(seconds=60)
    parked = _task(claimed_at=claimed_at, last_heartbeat_at=claimed_at)
    sibling = _task(status=TaskStatus.IN_PROGRESS, last_heartbeat_at=now)
    svc = AsyncMock()
    svc.unclaim_for_reaper = AsyncMock()

    released = await orch._maybe_release_parked_claim(svc, parked, [parked, sibling])

    assert released is False
    svc.unclaim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_parked_claim_with_no_other_task_left_to_dead_run_logic() -> None:
    """(c) The agent's only claim - nothing fresher to prove it's alive
    elsewhere - so this rule declines and leaves the decision to the
    existing dead-run logic further down the reaper."""
    orch = _orch()
    now = datetime.now(UTC)
    claimed_at = now - timedelta(seconds=1200)
    parked = _task(claimed_at=claimed_at, last_heartbeat_at=claimed_at)
    svc = AsyncMock()
    svc.unclaim_for_reaper = AsyncMock()

    released = await orch._maybe_release_parked_claim(svc, parked, [parked])

    assert released is False
    svc.unclaim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_parked_claim_blocked_by_inflight_registry() -> None:
    """(d) An unexpired in-flight entry for the claiming agent blocks release
    even though it is otherwise past-window and busy elsewhere - the claim
    may still be mid-finalization server-side."""
    orch = _orch()
    now = datetime.now(UTC)
    claimed_at = now - timedelta(seconds=1200)
    parked = _task(claimed_at=claimed_at, last_heartbeat_at=claimed_at)
    sibling = _task(status=TaskStatus.IN_PROGRESS, last_heartbeat_at=now)
    orch._mark_claim_in_flight("be-dev-1", "some-other-task")
    svc = AsyncMock()
    svc.unclaim_for_reaper = AsyncMock()

    released = await orch._maybe_release_parked_claim(svc, parked, [parked, sibling])

    assert released is False
    svc.unclaim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_parked_claim_ignores_advancing_heartbeat() -> None:
    """A heartbeat that advanced past the claim means genuine work - never
    treated as parked, regardless of siblings."""
    orch = _orch()
    now = datetime.now(UTC)
    claimed_at = now - timedelta(seconds=1200)
    working = _task(claimed_at=claimed_at, last_heartbeat_at=now - timedelta(seconds=5))
    sibling = _task(status=TaskStatus.IN_PROGRESS, last_heartbeat_at=now)
    svc = AsyncMock()
    svc.unclaim_for_reaper = AsyncMock()

    released = await orch._maybe_release_parked_claim(svc, working, [working, sibling])

    assert released is False
    svc.unclaim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_parked_claim_ignores_non_claimed_status() -> None:
    """The rule is scoped to `claimed` rows only - an in_progress task with a
    dead heartbeat is the existing reaper's territory, not this one's."""
    orch = _orch()
    now = datetime.now(UTC)
    claimed_at = now - timedelta(seconds=1200)
    task = _task(
        status=TaskStatus.IN_PROGRESS,
        claimed_at=claimed_at,
        last_heartbeat_at=claimed_at,
    )
    sibling = _task(status=TaskStatus.IN_PROGRESS, last_heartbeat_at=now)
    svc = AsyncMock()
    svc.unclaim_for_reaper = AsyncMock()

    released = await orch._maybe_release_parked_claim(svc, task, [task, sibling])

    assert released is False
    svc.unclaim_for_reaper.assert_not_awaited()


# ---------------------------------------------------------------------------
# Wired into the real reap loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reap_with_service_releases_parked_claim_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(a) integration: `_reap_with_service` itself, not just the helper,
    reaches the parked-claim release for the incident shape."""
    orch = _orch()
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    now = datetime.now(UTC)
    claimed_at = now - timedelta(seconds=1200)
    parked_a = _task(claimed_at=claimed_at, last_heartbeat_at=claimed_at)
    parked_b = _task(
        claimed_at=claimed_at + timedelta(seconds=30),
        last_heartbeat_at=claimed_at + timedelta(seconds=30),
    )
    working = _task(status=TaskStatus.IN_PROGRESS, last_heartbeat_at=now)
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [parked_a, parked_b, working]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    released_ids = {c.args[0] for c in svc.unclaim_for_reaper.await_args_list}
    assert released_ids == {parked_a.id, parked_b.id}


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

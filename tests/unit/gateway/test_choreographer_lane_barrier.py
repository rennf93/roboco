"""Per-dev lane barrier on the give_me_work -> claim path.

A developer with a pre-delegated sequenced code queue must not start a later
code leaf while an earlier same-assignee sibling is still open: that is the
out-of-order start that wedged the merge (a later PR cut from a base that
predates the earlier sibling's unmerged changes). ``i_am_idle`` already drops
lane-held leaves via ``_pending_not_lane_held``; this locks the same barrier
on ``give_me_work``'s pre-assigned path and on ``_run_claim_guards`` (the
direct claim verb), so neither route can jump the queue.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(task: AsyncMock) -> ChoreographerDeps:
    return ChoreographerDeps(
        task=task,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=AsyncMock(),
    )


def _dev_agent_task_svc() -> tuple[AsyncMock, UUID]:
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="developer")
    task_svc.list_pending_for_agent.return_value = []
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.list_in_progress_for_agent.return_value = []
    # Default: lane clear (no earlier incomplete sibling).
    task_svc.has_earlier_incomplete_code_sibling.return_value = False
    return task_svc, uuid4()


# ---------------------------------------------------------------------------
# give_me_work: pre-assigned path must drop a lane-held code leaf
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_give_me_work_skips_lane_held_pre_assigned_dev_task() -> None:
    """A pre-assigned pending code leaf sitting behind an earlier open
    same-assignee sibling is dropped (not offered); with nothing else
    available the dev goes idle rather than jumping its queue."""
    task_svc, agent_id = _dev_agent_task_svc()
    leaf = MagicMock(id=uuid4(), status="pending", title="later-leaf")
    task_svc.list_pending_for_agent.return_value = [leaf]
    task_svc.has_earlier_incomplete_code_sibling.return_value = True
    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["status"] == "idle"
    assert body["task_id"] is None
    task_svc.has_earlier_incomplete_code_sibling.assert_awaited_once_with(leaf)


@pytest.mark.asyncio
async def test_give_me_work_offers_pre_assigned_when_lane_clear() -> None:
    """A pre-assigned code leaf whose lane is clear (no earlier open sibling)
    is offered as normal — the filter only drops positively lane-held leaves."""
    task_svc, agent_id = _dev_agent_task_svc()
    leaf = MagicMock(id=uuid4(), status="pending", title="ready-leaf")
    task_svc.list_pending_for_agent.return_value = [leaf]
    task_svc.has_earlier_incomplete_code_sibling.return_value = False
    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["task_id"] == str(leaf.id)


@pytest.mark.asyncio
async def test_give_me_work_lane_filter_inert_under_partial_mock() -> None:
    """An AsyncMock stub returns a truthy non-bool (not ``True``); ``is not
    True`` keeps the filter inert so a partial test mock never drops a leaf
    it cannot positively confirm is lane-held."""
    task_svc, agent_id = _dev_agent_task_svc()
    leaf = MagicMock(id=uuid4(), status="pending", title="maybe-leaf")
    task_svc.list_pending_for_agent.return_value = [leaf]
    # Truthy stub, NOT the literal bool True -> inert (leaf kept).
    task_svc.has_earlier_incomplete_code_sibling.return_value = MagicMock()
    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["task_id"] == str(leaf.id)


# ---------------------------------------------------------------------------
# _run_claim_guards: a direct claim of a lane-held code task is refused
# ---------------------------------------------------------------------------


def _claim_task(
    *, task_type: str = "code", dependency_ids: list[Any] | None = None
) -> Any:
    return MagicMock(
        id=uuid4(),
        status="pending",
        assigned_to=uuid4(),
        parent_task_id=uuid4(),
        task_type=task_type,
        dependency_ids=dependency_ids or [],
        team="backend",
    )


@pytest.mark.asyncio
async def test_claim_guard_blocks_lane_held_code_task() -> None:
    """A direct claim of a code leaf with an earlier open same-assignee
    sibling is refused (invalid_state) and parked back to pending."""
    task_svc, agent_id = _dev_agent_task_svc()
    task = _claim_task()
    task_svc.get.return_value = task
    task_svc.has_earlier_incomplete_code_sibling.return_value = True
    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    guard = await c._run_claim_guards(
        agent_id=agent_id, task=task, role_str="developer"
    )
    assert guard is not None
    assert guard.error == "invalid_state"
    task_svc.release_dependency_blocked_claim.assert_awaited_once_with(task.id)


@pytest.mark.asyncio
async def test_claim_guard_allows_when_lane_clear() -> None:
    """A code leaf whose lane is clear proceeds (no rejection)."""
    task_svc, agent_id = _dev_agent_task_svc()
    task = _claim_task()
    task_svc.get.return_value = task
    task_svc.has_earlier_incomplete_code_sibling.return_value = False
    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    guard = await c._run_claim_guards(
        agent_id=agent_id, task=task, role_str="developer"
    )
    assert guard is None
    task_svc.release_dependency_blocked_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_guard_fail_closed_on_lookup_error() -> None:
    """If the lane lookup raises, the claim is refused (fail-closed) rather
    than letting an out-of-order start through on a DB hiccup."""
    task_svc, agent_id = _dev_agent_task_svc()
    task = _claim_task()
    task_svc.get.return_value = task
    task_svc.has_earlier_incomplete_code_sibling.side_effect = RuntimeError("db down")
    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    guard = await c._run_claim_guards(
        agent_id=agent_id, task=task, role_str="developer"
    )
    assert guard is not None
    assert guard.error == "invalid_state"


@pytest.mark.asyncio
async def test_claim_guard_lane_inert_for_non_code_task() -> None:
    """A non-code task (e.g. planning) is not lane-ordered; even if the
    predicate were to return True the guard must not block a coordinator's
    non-code claim — the lane is code-only. Predicate False -> proceed."""
    task_svc, agent_id = _dev_agent_task_svc()
    task = _claim_task(task_type="planning")
    task_svc.get.return_value = task
    task_svc.has_earlier_incomplete_code_sibling.return_value = False
    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    guard = await c._run_claim_guards(agent_id=agent_id, task=task, role_str="main_pm")
    assert guard is None

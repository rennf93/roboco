"""One in-flight claim per agent - the 2026-09-05 triple-claim fix.

Live production, 2026-09-05 22:08 UTC: the dispatcher routed three pending
backend tasks to the same idle be-dev-1 within 60 seconds. Each claim runs
`_finalize_claim`, which sets `agent.current_task_id` then creates the
task's branch (53s+ on that host); the second and third claims piled up
behind the first's row lock and eventually died on `lock_timeout` while
be-dev-2 sat idle. `_is_agent_active` cannot see this window - the container
isn't up yet - so `_select_agent_for_cell` kept re-selecting the same agent.

`self._claims_in_flight` (agent slug -> (task_id, monotonic deadline)) closes
it: an agent is parked the moment its claim is issued and freed once the
claim has a definite outcome, or after `dispatch_claim_inflight_ttl_seconds`
on a client-side timeout/exception (the server may still be finishing).
"""

from __future__ import annotations

import time
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator, AgentReadinessError


def _orch() -> AgentOrchestrator:
    # AgentOrchestrator.__new__ pre-seeds _instances / _claims_in_flight for
    # exactly this bypass-__init__ pattern.
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


def _any(orch: AgentOrchestrator) -> Any:
    """Untyped view for stubbing methods in these tests - avoids per-line
    method-assign noise without ever reaching for `# type: ignore`."""
    return cast("Any", orch)


def _pending_task(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "status": "pending",
        "team": "backend",
        "task_type": "code",
        "title": "Some code task",
        "assigned_to": None,
        "created_by": None,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# (a) / (b) - _select_agent_for_cell treats an in-flight entry like active
# ---------------------------------------------------------------------------


def test_inflight_agent_skipped_in_favor_of_next_candidate() -> None:
    """(a) A second pending task this tick must not land on an agent whose
    claim from the first task is still in flight."""
    orch = _orch()
    _any(orch)._is_agent_active = lambda _aid: False
    orch._mark_claim_in_flight("be-dev-1", "task-1")

    assert orch._select_agent_for_cell("backend", "dev") == "be-dev-2"


def test_agent_eligible_again_after_claim_clears() -> None:
    """(b) Once the claim resolves (`_clear_claim_in_flight`), the agent is a
    normal candidate again."""
    orch = _orch()
    _any(orch)._is_agent_active = lambda _aid: False
    orch._mark_claim_in_flight("be-dev-1", "task-1")
    orch._clear_claim_in_flight("be-dev-1")

    assert orch._select_agent_for_cell("backend", "dev") == "be-dev-1"


def test_inflight_entry_expires_lazily_past_its_deadline() -> None:
    """Expiry is lazy-on-read: a past-deadline entry is dropped without a
    background sweep."""
    orch = _orch()
    orch._mark_claim_in_flight("be-dev-1", "task-1")
    orch._claims_in_flight["be-dev-1"] = ("task-1", time.monotonic() - 1)

    assert orch._is_claim_in_flight("be-dev-1") is False
    assert "be-dev-1" not in orch._claims_in_flight


# ---------------------------------------------------------------------------
# (d) - every dev candidate in flight -> None, never candidates[0]
# ---------------------------------------------------------------------------


def test_all_dev_candidates_inflight_returns_none_not_first_candidate() -> None:
    """(d) Both dev slots busy (in flight, not necessarily active) -> the
    selector returns None so the task waits for the next scan, instead of
    the old 'return candidates[0]' fallback stacking a claim."""
    orch = _orch()
    _any(orch)._is_agent_active = lambda _aid: False
    orch._mark_claim_in_flight("be-dev-1", "task-1")
    orch._mark_claim_in_flight("be-dev-2", "task-2")

    assert orch._select_agent_for_cell("backend", "dev") is None


def test_mixed_active_and_inflight_returns_none() -> None:
    """One candidate active, the other in flight - still nothing free."""
    orch = _orch()
    _any(orch)._is_agent_active = lambda aid: aid == "be-dev-1"
    orch._mark_claim_in_flight("be-dev-2", "task-2")

    assert orch._select_agent_for_cell("backend", "dev") is None


@pytest.mark.asyncio
async def test_dev_routing_defers_when_all_cell_devs_busy_not_main_pm() -> None:
    """(d) End-to-end: `_get_routing_target` for a busy cell must return None
    (defer to next scan), never main-pm - MAIN_PM_NO_CODE would refuse a
    code claim there forever."""
    orch = _orch()
    _any(orch)._is_agent_active = lambda _aid: False
    orch._mark_claim_in_flight("be-dev-1", "task-1")
    orch._mark_claim_in_flight("be-dev-2", "task-2")

    result = await orch._get_routing_target(
        "dev", {"id": "t3", "team": "backend", "task_type": "code"}
    )

    assert result is None


@pytest.mark.asyncio
async def test_route_unassigned_pm_task_claims_nothing_when_all_devs_busy() -> None:
    """(d) Full dispatch path: with both dev candidates in flight, routing a
    fresh pending task must not call claim or spawn at all."""
    orch = _orch()
    oa = _any(orch)
    oa._is_agent_active = lambda _aid: False
    orch._mark_claim_in_flight("be-dev-1", "task-1")
    orch._mark_claim_in_flight("be-dev-2", "task-2")
    task = _pending_task()
    client = cast("Any", object())

    claim = AsyncMock()
    spawn = AsyncMock()
    oa._pending_claim_blocked = AsyncMock(return_value=False)
    oa._task_has_children = AsyncMock(return_value=False)
    oa._claim_task_for_agent = claim
    oa.spawn_agent = spawn

    await orch._route_unassigned_pm_task(client, task)

    claim.assert_not_awaited()
    spawn.assert_not_awaited()


# ---------------------------------------------------------------------------
# (c) - a client-side timeout/exception keeps the agent parked until the TTL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_dev_claim_marks_inflight_and_spawns_then_clears() -> None:
    """Happy path bracket: claim succeeds -> spawn issued -> entry cleared."""
    orch = _orch()
    oa = _any(orch)
    task = _pending_task()
    client = cast("Any", object())
    with_kwargs: dict[str, Any] = {}

    async def fake_spawn(**kwargs: Any) -> None:
        with_kwargs.update(kwargs)

    oa._pending_claim_blocked = AsyncMock(return_value=False)
    oa._classify_task_routing = lambda *_a, **_k: "dev"
    oa._get_routing_target = AsyncMock(return_value="be-dev-1")
    oa._is_agent_active = lambda _aid: False
    oa._creator_route_should_skip = lambda *_a, **_k: False
    oa._task_git_context = lambda _t: None
    oa._pm_spawn_prompt = AsyncMock(return_value="do it")
    oa._claim_task_for_agent = AsyncMock(return_value=True)
    oa.spawn_agent = AsyncMock(side_effect=fake_spawn)

    await orch._route_unassigned_pm_task(client, task)

    assert with_kwargs.get("agent_id") == "be-dev-1"
    assert orch._is_claim_in_flight("be-dev-1") is False


@pytest.mark.asyncio
async def test_route_dev_claim_timeout_keeps_agent_parked_until_ttl() -> None:
    """(c) `_claim_task_for_agent` returning None (client-side exception/
    timeout) must NOT clear the in-flight entry - the server may still be
    finishing the claim. The agent stays ineligible until the TTL passes."""
    orch = _orch()
    oa = _any(orch)
    task = _pending_task()
    client = cast("Any", object())

    oa._pending_claim_blocked = AsyncMock(return_value=False)
    oa._classify_task_routing = lambda *_a, **_k: "dev"
    oa._get_routing_target = AsyncMock(return_value="be-dev-1")
    oa._is_agent_active = lambda _aid: False
    oa._creator_route_should_skip = lambda *_a, **_k: False
    oa._claim_task_for_agent = AsyncMock(return_value=None)
    spawn = AsyncMock()
    oa.spawn_agent = spawn

    await orch._route_unassigned_pm_task(client, task)

    spawn.assert_not_awaited()
    assert orch._is_claim_in_flight("be-dev-1") is True

    # TTL elapses -> the agent is free again.
    orch._claims_in_flight["be-dev-1"] = ("x", time.monotonic() - 1)
    assert orch._is_claim_in_flight("be-dev-1") is False


@pytest.mark.asyncio
async def test_route_dev_claim_definite_failure_clears_immediately() -> None:
    """A definite server rejection (False, not None) frees the agent right
    away - no need to wait for the TTL."""
    orch = _orch()
    oa = _any(orch)
    task = _pending_task()
    client = cast("Any", object())

    oa._pending_claim_blocked = AsyncMock(return_value=False)
    oa._classify_task_routing = lambda *_a, **_k: "dev"
    oa._get_routing_target = AsyncMock(return_value="be-dev-1")
    oa._is_agent_active = lambda _aid: False
    oa._creator_route_should_skip = lambda *_a, **_k: False
    oa._claim_task_for_agent = AsyncMock(return_value=False)
    spawn = AsyncMock()
    oa.spawn_agent = spawn

    await orch._route_unassigned_pm_task(client, task)

    spawn.assert_not_awaited()
    assert orch._is_claim_in_flight("be-dev-1") is False


@pytest.mark.asyncio
async def test_auto_assign_doc_brackets_claim_the_same_way() -> None:
    """The doc-dispatch pre-claim call site gets the identical bracket."""
    orch = _orch()
    oa = _any(orch)
    task = _pending_task(team="backend")
    client = cast("Any", object())

    oa._select_agent_for_cell = lambda *_a, **_k: "be-doc"
    oa._is_agent_active = lambda _aid: False
    oa._pm_respawn_should_gate = AsyncMock(return_value=False)
    oa._build_doc_prompt = lambda _t: "doc it"
    oa._task_git_context = lambda _t: None
    oa._claim_task_for_agent = AsyncMock(return_value=None)
    spawn = AsyncMock()
    oa.spawn_agent = spawn

    await orch._auto_assign_doc(client, task, "backend")

    spawn.assert_not_awaited()
    assert orch._is_claim_in_flight("be-doc") is True


@pytest.mark.asyncio
async def test_auto_assign_doc_claim_rejection_clears_immediately() -> None:
    """A definite server rejection (False) on the doc call site frees the
    agent right away, same as the dev-routing call site."""
    orch = _orch()
    oa = _any(orch)
    task = _pending_task(team="backend")
    client = cast("Any", object())

    oa._select_agent_for_cell = lambda *_a, **_k: "be-doc"
    oa._is_agent_active = lambda _aid: False
    oa._pm_respawn_should_gate = AsyncMock(return_value=False)
    oa._claim_task_for_agent = AsyncMock(return_value=False)
    spawn = AsyncMock()
    oa.spawn_agent = spawn

    await orch._auto_assign_doc(client, task, "backend")

    spawn.assert_not_awaited()
    assert orch._is_claim_in_flight("be-doc") is False


# ---------------------------------------------------------------------------
# The bug this leg fixes - spawn_agent raising AgentReadinessError on an
# ordinary "not spawn-ready" path used to leak the in-flight entry, since
# `_clear_claim_in_flight` sat as the statement *after* `spawn_agent` with no
# try/finally around it. Both call sites must clear on the way out via the
# exception, not just on a clean return.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_dev_spawn_raises_still_clears_inflight_entry() -> None:
    """(a) Claim succeeds, `spawn_agent` raises `AgentReadinessError` (an
    ordinary not-yet-ready outcome) - the exception must propagate AND the
    in-flight entry must be cleared, not leaked for the full TTL."""
    orch = _orch()
    oa = _any(orch)
    task = _pending_task()
    client = cast("Any", object())

    oa._pending_claim_blocked = AsyncMock(return_value=False)
    oa._classify_task_routing = lambda *_a, **_k: "dev"
    oa._get_routing_target = AsyncMock(return_value="be-dev-1")
    oa._is_agent_active = lambda _aid: False
    oa._creator_route_should_skip = lambda *_a, **_k: False
    oa._task_git_context = lambda _t: None
    oa._pm_spawn_prompt = AsyncMock(return_value="do it")
    oa._claim_task_for_agent = AsyncMock(return_value=True)
    oa.spawn_agent = AsyncMock(side_effect=AgentReadinessError("not ready"))

    with pytest.raises(AgentReadinessError):
        await orch._route_unassigned_pm_task(client, task)

    assert orch._is_claim_in_flight("be-dev-1") is False


@pytest.mark.asyncio
async def test_auto_assign_doc_spawn_raises_still_clears_inflight_entry() -> None:
    """(a) Same bracket, doc call site: `spawn_agent` raising must not leak
    the in-flight entry."""
    orch = _orch()
    oa = _any(orch)
    task = _pending_task(team="backend")
    client = cast("Any", object())

    oa._select_agent_for_cell = lambda *_a, **_k: "be-doc"
    oa._is_agent_active = lambda _aid: False
    oa._pm_respawn_should_gate = AsyncMock(return_value=False)
    oa._build_doc_prompt = lambda _t: "doc it"
    oa._task_git_context = lambda _t: None
    oa._claim_task_for_agent = AsyncMock(return_value=True)
    oa.spawn_agent = AsyncMock(side_effect=AgentReadinessError("not ready"))

    with pytest.raises(AgentReadinessError):
        await orch._auto_assign_doc(client, task, "backend")

    assert orch._is_claim_in_flight("be-doc") is False


# ---------------------------------------------------------------------------
# _claim_task_for_agent - the tri-state return itself
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


@pytest.mark.asyncio
async def test_claim_task_for_agent_returns_true_on_200() -> None:
    orch = _orch()
    client = AsyncMock()
    client.post = AsyncMock(return_value=_Resp(200))

    assert await orch._claim_task_for_agent(client, "t1", "be-dev-1") is True


@pytest.mark.asyncio
async def test_claim_task_for_agent_returns_false_on_rejection() -> None:
    orch = _orch()
    client = AsyncMock()
    client.post = AsyncMock(return_value=_Resp(409))

    assert await orch._claim_task_for_agent(client, "t1", "be-dev-1") is False


@pytest.mark.asyncio
async def test_claim_task_for_agent_returns_none_on_exception() -> None:
    """A client-side exception (timeout included) is ambiguous - not a clean
    failure - so it returns None, distinct from a definite server reject."""
    orch = _orch()
    client = AsyncMock()
    client.post = AsyncMock(side_effect=TimeoutError("boom"))

    assert await orch._claim_task_for_agent(client, "t1", "be-dev-1") is None


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

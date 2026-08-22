"""assign-review-pm dispatch seam — the recovery half of the pr_pass
ownership-clearing fix.

CLAIM_RULES has no claim() edge into AWAITING_PM_REVIEW (the i_will_plan
re-claim-loop fix, #740/d87e2d9b) — pr_pass hands off to the owning PM
directly now (``TaskService.pr_pass``), but an unassigned task from before
the fix, or a block/escalate/unblock(restore=True) round trip landing on a
stale owner, still needs correcting before the dispatcher spawns a PM that
can't pass its own ownership guard.

``_ensure_review_pm_assigned`` is the raw route call — it reports the
route's own outcome only (``None`` on ANY rejection/transport error, never a
stale fallback baked in). Its two callers each own their own fallback
policy: ``_closure_review_pm`` (``_maybe_spawn_pm_closure``) keeps its
already-known-correct team-resolved default on any failure, since a stale
``assigned_to`` fallback there could clobber it and spawn the wrong PM;
``_review_pm_slug`` (``_dispatch_pm_review_work``) has no better default
than the task's own ``assigned_to`` and also pre-checks it to skip the row
lock + HTTP round trip when already correct.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


def _orch_any() -> Any:
    """Untyped handle for tests that stub methods via direct attribute
    assignment (mypy's method-assign check only fires on the concrete
    ``AgentOrchestrator`` type; ``patch.object``-based tests use ``_orch()``
    instead)."""
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


def _review_task(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "status": "awaiting_pm_review",
        "team": "backend",
        "assigned_to": None,
    }
    base.update(over)
    return base


def _client_with_response(status_code: int, body: dict[str, Any]) -> Any:
    resp = MagicMock(status_code=status_code)
    resp.json.return_value = body
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    return client


# ---------------------------------------------------------------------------
# _ensure_review_pm_assigned — the route call, no fallback baked in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_review_pm_assigned_resolves_slug_on_success() -> None:
    orch = _orch()
    task = _review_task()
    pm_uuid = str(uuid4())
    client = _client_with_response(200, {"assigned_to": pm_uuid})

    with patch.object(orch, "_resolve_agent_slug", return_value="be-pm") as resolve:
        result = await orch._ensure_review_pm_assigned(client, task)

    assert result == "be-pm"
    client.post.assert_awaited_once_with(
        f"{settings.internal_api_url}/tasks/{task['id']}/assign-review-pm"
    )
    resolve.assert_called_once_with(pm_uuid)


@pytest.mark.asyncio
async def test_ensure_review_pm_assigned_none_when_endpoint_returns_no_owner() -> None:
    """An unresolvable PM (assign_review_pm's own fallback) means no owner —
    nothing to spawn this tick."""
    orch = _orch()
    task = _review_task()
    client = _client_with_response(200, {"assigned_to": None})

    result = await orch._ensure_review_pm_assigned(client, task)

    assert result is None


@pytest.mark.asyncio
async def test_ensure_review_pm_assigned_none_on_rejection_no_stale_fallback() -> None:
    """A non-200 must report failure cleanly — NOT fall back to the task's
    own (possibly stale) assigned_to. A blind fallback here is exactly what
    let a transient failure clobber _closure_review_pm's already-correct
    default in the pre-fix version of this seam."""
    orch = _orch()
    stale_pm = str(uuid4())
    task = _review_task(assigned_to=stale_pm)
    client = _client_with_response(500, {})

    with patch.object(orch, "_resolve_agent_slug") as resolve:
        result = await orch._ensure_review_pm_assigned(client, task)

    assert result is None
    resolve.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_review_pm_assigned_none_on_transport_error() -> None:
    orch = _orch()
    task = _review_task(assigned_to=str(uuid4()))
    client = MagicMock()
    client.post = AsyncMock(side_effect=RuntimeError("connection reset"))

    result = await orch._ensure_review_pm_assigned(client, task)

    assert result is None


# ---------------------------------------------------------------------------
# _closure_review_pm — _maybe_spawn_pm_closure's caller-owned fallback: the
# team-resolved default must survive ANY ensure-call failure.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closure_review_pm_adopts_resolved_value_on_success() -> None:
    orch = _orch()
    task = _review_task()
    with patch.object(
        orch, "_ensure_review_pm_assigned", new=AsyncMock(return_value="be-pm")
    ):
        result = await orch._closure_review_pm(cast("Any", object()), task, "main-pm")
    assert result == "be-pm"


@pytest.mark.asyncio
async def test_closure_review_pm_keeps_default_on_route_failure() -> None:
    """The exact critic-flagged regression: a transient assign-review-pm
    failure must NOT overwrite the already-correct team-resolved pm_id with
    a stale fallback (e.g. main-pm on a task that should be be-pm)."""
    orch = _orch()
    task = _review_task()
    with patch.object(
        orch, "_ensure_review_pm_assigned", new=AsyncMock(return_value=None)
    ) as ensure:
        result = await orch._closure_review_pm(cast("Any", object()), task, "be-pm")
    assert result == "be-pm"
    ensure.assert_awaited_once()


@pytest.mark.asyncio
async def test_closure_review_pm_skips_ensure_outside_review_status() -> None:
    """claimed/in_progress/paused parents got their PM from the normal
    claim/delegate flow — no correction, no route call at all."""
    orch = _orch()
    task = _review_task(status="in_progress")
    with patch.object(orch, "_ensure_review_pm_assigned", new=AsyncMock()) as ensure:
        result = await orch._closure_review_pm(cast("Any", object()), task, "be-pm")
    assert result == "be-pm"
    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_spawn_pm_closure_spawns_team_resolved_pm_on_route_failure() -> (
    None
):
    """End-to-end: _maybe_spawn_pm_closure must still spawn the correct
    (team-resolved) PM when the assign-review-pm route fails outright."""
    orch = _orch_any()
    orch._is_recently_paused = MagicMock(return_value=False)
    orch._fetch_all_descendants = AsyncMock(
        return_value=[{"id": "leaf", "status": "completed"}]
    )
    orch._all_descendants_terminal = MagicMock(return_value=True)
    orch._already_promoted_for_closure = MagicMock(return_value=False)
    orch._closure_pm_for_team = MagicMock(return_value="be-pm")
    orch._is_agent_active = MagicMock(return_value=False)
    orch._closure_handled_without_pm = AsyncMock(return_value=(False, None))
    orch._build_pm_closure_prompt = MagicMock(return_value="PROMPT")
    orch._task_git_context = MagicMock(return_value=None)
    orch.spawn_agent = AsyncMock()
    orch._ensure_review_pm_assigned = AsyncMock(return_value=None)  # route failed
    task = {
        "id": "parent-1",
        "status": "awaiting_pm_review",
        "team": "backend",
        "assigned_to": str(uuid4()),  # some stale value the fix must ignore
    }

    await orch._maybe_spawn_pm_closure(cast("Any", object()), task)

    orch.spawn_agent.assert_awaited_once()
    assert orch.spawn_agent.await_args.kwargs["agent_id"] == "be-pm"


# ---------------------------------------------------------------------------
# _review_pm_slug — _dispatch_pm_review_work's pre-check + own fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_pm_slug_skips_route_when_already_correct() -> None:
    """The FINDING-3 pre-check: assigned_to already names the team-resolved
    owner, so no row-lock + HTTP round trip is needed this tick."""
    orch = _orch()
    pm_uuid = str(uuid4())
    task = _review_task(assigned_to=pm_uuid)
    with (
        patch.object(orch, "_closure_pm_for_team", return_value="be-pm"),
        patch.object(orch, "_resolve_agent_slug", return_value="be-pm"),
        patch.object(orch, "_ensure_review_pm_assigned", new=AsyncMock()) as ensure,
    ):
        result = await orch._review_pm_slug(cast("Any", object()), task)
    assert result == "be-pm"
    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_pm_slug_corrects_mismatch_via_route() -> None:
    orch = _orch()
    task = _review_task(assigned_to=str(uuid4()))  # stale/wrong owner
    with (
        patch.object(orch, "_closure_pm_for_team", return_value="be-pm"),
        patch.object(orch, "_resolve_agent_slug", return_value="main-pm"),
        patch.object(
            orch, "_ensure_review_pm_assigned", new=AsyncMock(return_value="be-pm")
        ) as ensure,
    ):
        result = await orch._review_pm_slug(cast("Any", object()), task)
    assert result == "be-pm"
    ensure.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_pm_slug_falls_back_to_current_on_route_failure() -> None:
    """No independently-known-better default here (unlike _closure_review_pm)
    — falling back to the task's own current assignee is correct."""
    orch = _orch()
    stale_pm_uuid = str(uuid4())
    task = _review_task(assigned_to=stale_pm_uuid)
    with (
        patch.object(orch, "_closure_pm_for_team", return_value="be-pm"),
        patch.object(orch, "_resolve_agent_slug", return_value="main-pm"),
        patch.object(
            orch, "_ensure_review_pm_assigned", new=AsyncMock(return_value=None)
        ),
    ):
        result = await orch._review_pm_slug(cast("Any", object()), task)
    assert result == "main-pm"


@pytest.mark.asyncio
async def test_review_pm_slug_none_when_unassigned_and_route_fails() -> None:
    orch = _orch()
    task = _review_task(assigned_to=None)
    with (
        patch.object(orch, "_closure_pm_for_team", return_value="be-pm"),
        patch.object(
            orch, "_ensure_review_pm_assigned", new=AsyncMock(return_value=None)
        ),
    ):
        result = await orch._review_pm_slug(cast("Any", object()), task)
    assert result is None


# ---------------------------------------------------------------------------
# _dispatch_pm_review_work — the seam replaces the old claim-then-spawn split
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_pm_review_work_spawns_resolved_pm() -> None:
    orch = _orch()
    task = _review_task()
    client = cast("Any", object())

    with (
        patch.object(orch, "_fetch_tasks", new=AsyncMock(return_value=[task])),
        patch.object(
            orch, "_blocked_by_earlier_sibling", new=AsyncMock(return_value=False)
        ),
        patch.object(orch, "_review_pm_slug", new=AsyncMock(return_value="be-pm")),
        patch("roboco.runtime.orchestrator.is_spawnable_agent_slug", return_value=True),
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(
            orch, "_pm_respawn_should_gate", new=AsyncMock(return_value=False)
        ),
        patch.object(orch, "_build_pm_review_prompt", return_value="prompt"),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_pm_review_work(client)

    spawn.assert_awaited_once()
    assert spawn.call_args.kwargs["agent_id"] == "be-pm"
    assert spawn.call_args.kwargs["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_dispatch_pm_review_work_skips_when_pm_unresolvable() -> None:
    orch = _orch()
    task = _review_task()
    client = cast("Any", object())

    with (
        patch.object(orch, "_fetch_tasks", new=AsyncMock(return_value=[task])),
        patch.object(
            orch, "_blocked_by_earlier_sibling", new=AsyncMock(return_value=False)
        ),
        patch.object(orch, "_review_pm_slug", new=AsyncMock(return_value=None)),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_pm_review_work(client)

    spawn.assert_not_awaited()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

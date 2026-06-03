"""Board agents (Product Owner + Head of Marketing) review board-team tasks.

Cluster C5:
- A board/coordination task is a TWO-reviewer gate: BOTH the Product Owner and
  the Head of Marketing must review it before it reaches the CEO (finding #4).
  Each reviewer is dispatched ONCE — board roles have no verb to claim/plan/
  delegate/complete, so a respawn cannot advance the task and would just loop.
- Once BOTH reviewers have finished, the orchestrator emits exactly ONE formal
  CEO notification so the handoff to Approve & Start is an actionable signal,
  not buried channel chatter (finding #2).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._board_dispatched = set()
    orch._board_review_ceo_notified = set()
    return orch


def _board_task(assigned_to: str) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "title": "Strategic feature",
        "description": "A board-level task to review and shape.",
        "assigned_to": assigned_to,
    }


@pytest.mark.asyncio
async def test_both_board_agents_dispatched_for_board_task() -> None:
    """A board task must dispatch BOTH the PO and the Head of Marketing — the
    review is a two-reviewer gate, not a single-assignee claim (finding #4)."""
    orch = _make_orch()
    task = _board_task("product-owner")
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(
            orch,
            "_maybe_notify_ceo_board_review_complete",
            new=AsyncMock(),
        ),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._handle_board_assigned_task(task, "product-owner")

    dispatched = {call.kwargs["agent_id"] for call in spawn.await_args_list}
    assert dispatched == {"product-owner", "head-marketing"}
    for call in spawn.await_args_list:
        assert call.kwargs["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_each_board_agent_spawned_only_once() -> None:
    """Board roles have no progression verb — a re-tick must NOT respawn."""
    orch = _make_orch()
    task = _board_task("head-marketing")
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(
            orch,
            "_maybe_notify_ceo_board_review_complete",
            new=AsyncMock(),
        ),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._handle_board_assigned_task(task, "head-marketing")
        # Second tick: still pending — must not respawn either reviewer.
        await orch._handle_board_assigned_task(task, "head-marketing")

    dispatched = [call.kwargs["agent_id"] for call in spawn.await_args_list]
    assert sorted(dispatched) == ["head-marketing", "product-owner"]


@pytest.mark.asyncio
async def test_board_handler_skips_active_reviewer_but_dispatches_other() -> None:
    """An already-running reviewer is skipped; the other is still dispatched."""
    orch = _make_orch()
    task = _board_task("product-owner")

    def _active(slug: str) -> bool:
        return slug == "product-owner"

    with (
        patch.object(orch, "_is_agent_active", side_effect=_active),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(
            orch,
            "_maybe_notify_ceo_board_review_complete",
            new=AsyncMock(),
        ),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._handle_board_assigned_task(task, "product-owner")

    dispatched = {call.kwargs["agent_id"] for call in spawn.await_args_list}
    assert dispatched == {"head-marketing"}


@pytest.mark.asyncio
async def test_board_handler_ignores_non_board_assignee() -> None:
    orch = _make_orch()
    task = _board_task("be-pm")
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._handle_board_assigned_task(task, "be-pm")
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_unassigned_board_task_dispatches_both_via_board_handler() -> None:
    """An UNASSIGNED board task must route through the board handler so BOTH
    reviewers are dispatched — not claimed + single-spawned for the PO only
    (finding #4). The task stays unclaimed for the CEO's Approve & Start."""
    orch = _make_orch()
    task = {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "task_type": "code",
        "title": "Strategic feature",
        "description": "A board-level task to review and shape.",
        "assigned_to": None,
    }
    client = object()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(
            orch,
            "_maybe_notify_ceo_board_review_complete",
            new=AsyncMock(),
        ),
        patch.object(
            orch, "_claim_task_for_agent", new=AsyncMock(return_value=True)
        ) as claim,
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._route_unassigned_pm_task(client, task)

    # Board work is a two-reviewer gate, not a claim — never claimed here.
    claim.assert_not_awaited()
    dispatched = {call.kwargs["agent_id"] for call in spawn.await_args_list}
    assert dispatched == {"product-owner", "head-marketing"}


def test_board_review_complete_requires_both_reviewers_idle() -> None:
    """Review is complete only when BOTH reviewers are dispatched AND idle."""
    orch = _make_orch()
    task_id = str(uuid4())

    with patch.object(orch, "_is_agent_active", return_value=False):
        # Neither dispatched yet.
        assert orch._board_review_complete(task_id) is False
        # Only PO dispatched.
        orch._board_dispatched.add(("product-owner", task_id))
        assert orch._board_review_complete(task_id) is False
        # Both dispatched and idle.
        orch._board_dispatched.add(("head-marketing", task_id))
        assert orch._board_review_complete(task_id) is True


def test_board_review_not_complete_while_a_reviewer_active() -> None:
    orch = _make_orch()
    task_id = str(uuid4())
    orch._board_dispatched.add(("product-owner", task_id))
    orch._board_dispatched.add(("head-marketing", task_id))

    with patch.object(
        orch, "_is_agent_active", side_effect=lambda s: s == "head-marketing"
    ):
        # HoM still running its review — not done yet.
        assert orch._board_review_complete(task_id) is False


@pytest.mark.asyncio
async def test_ceo_notified_once_when_board_review_complete() -> None:
    """Finding #2: a formal CEO notification fires exactly once when both
    board reviewers have finished."""
    orch = _make_orch()
    task_id = str(uuid4())
    orch._board_dispatched.add(("product-owner", task_id))
    orch._board_dispatched.add(("head-marketing", task_id))

    svc = AsyncMock()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch(
            "roboco.services.notification.NotificationService",
            return_value=svc,
        ),
    ):
        await orch._maybe_notify_ceo_board_review_complete(task_id)
        # Second tick: already notified — must not re-emit.
        await orch._maybe_notify_ceo_board_review_complete(task_id)

    svc.send_board_review_complete_notification.assert_awaited_once_with(
        task_id=task_id
    )
    assert task_id in orch._board_review_ceo_notified


@pytest.mark.asyncio
async def test_ceo_not_notified_while_review_incomplete() -> None:
    """No CEO notification until BOTH reviewers are done."""
    orch = _make_orch()
    task_id = str(uuid4())
    # Only PO has been dispatched/finished.
    orch._board_dispatched.add(("product-owner", task_id))

    svc = AsyncMock()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch(
            "roboco.services.notification.NotificationService",
            return_value=svc,
        ),
    ):
        await orch._maybe_notify_ceo_board_review_complete(task_id)

    svc.send_board_review_complete_notification.assert_not_awaited()
    assert task_id not in orch._board_review_ceo_notified


@pytest.mark.asyncio
async def test_ceo_notify_failure_allows_retry() -> None:
    """A notification failure clears the one-shot guard so a later tick retries."""
    orch = _make_orch()
    task_id = str(uuid4())
    orch._board_dispatched.add(("product-owner", task_id))
    orch._board_dispatched.add(("head-marketing", task_id))

    svc = AsyncMock()
    svc.send_board_review_complete_notification.side_effect = RuntimeError("db down")
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch(
            "roboco.services.notification.NotificationService",
            return_value=svc,
        ),
    ):
        await orch._maybe_notify_ceo_board_review_complete(task_id)

    # Guard cleared so a later, healthy tick can re-emit.
    assert task_id not in orch._board_review_ceo_notified


def test_board_review_prompt_names_both_reviewers_and_board_verbs() -> None:
    """The prompt must steer board agents to their real verbs (triage / note /
    say / i_am_idle), make the PO+HoM pair-review model explicit, and away from
    claim/plan/delegate they do not have."""
    orch = _make_orch()
    prompt = orch._build_board_prompt(_board_task("product-owner"))
    assert "triage()" in prompt
    assert "note(" in prompt
    assert "i_am_idle()" in prompt
    assert "Product Owner" in prompt and "Head of Marketing" in prompt
    assert "do NOT" in prompt.lower() or "do not" in prompt.lower()

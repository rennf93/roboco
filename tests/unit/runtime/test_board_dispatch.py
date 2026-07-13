"""Board agents (Product Owner + Head of Marketing) review board-team tasks.

- A board/coordination task is a TWO-reviewer gate: BOTH the Product Owner and
  the Head of Marketing must review it before it reaches the CEO. Each reviewer
  is dispatched ONCE — board roles have no verb to claim/plan/delegate/complete,
  so a respawn cannot advance the task and would just loop.
- Once BOTH reviewers have finished, the orchestrator hands the review to the
  CEO: it flags the (still-pending) task ``board_review_complete`` so the CEO's
  Approve & Start button appears, and emits exactly one formal CEO notification
  so the handoff is an actionable signal, not buried channel chatter.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx
from roboco.runtime.orchestrator import AgentOrchestrator


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
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


def _patch_handoff_db(task_svc: AsyncMock) -> tuple[Any, Any]:
    """Patch the DB context + TaskService the handoff opens to flag the task.

    Returns a tuple of context managers for the caller's ``with`` block so the
    direct-call tests exercise the real handoff body without touching a DB.
    """

    @asynccontextmanager
    async def _fake_ctx() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    return (
        patch("roboco.db.base.get_db_context", _fake_ctx),
        patch("roboco.services.task.TaskService", return_value=task_svc),
    )


@pytest.mark.asyncio
async def test_both_board_agents_dispatched_for_board_task() -> None:
    """A board task must dispatch BOTH the PO and the Head of Marketing — the
    review is a two-reviewer gate, not a single-assignee claim."""
    orch = _make_orch()
    task = _board_task("product-owner")
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(
            orch,
            "_maybe_handoff_board_review_to_ceo",
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
            "_maybe_handoff_board_review_to_ceo",
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
            "_maybe_handoff_board_review_to_ceo",
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
    reviewers are dispatched — not claimed + single-spawned for the PO only.
    The task stays unclaimed for the CEO's Approve & Start."""
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
    client = cast("httpx.AsyncClient", object())
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(
            orch,
            "_maybe_handoff_board_review_to_ceo",
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
async def test_ceo_handoff_once_when_board_review_complete() -> None:
    """When both reviewers finish, the handoff flags the task board-reviewed and
    fires exactly one CEO notification."""
    orch = _make_orch()
    task_id = str(uuid4())
    orch._board_dispatched.add(("product-owner", task_id))
    orch._board_dispatched.add(("head-marketing", task_id))

    svc = AsyncMock()
    task_svc = AsyncMock()
    db_ctx, task_ctx = _patch_handoff_db(task_svc)
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch("roboco.services.notification.NotificationService", return_value=svc),
        db_ctx,
        task_ctx,
    ):
        await orch._maybe_handoff_board_review_to_ceo(task_id)
        # Second tick: already handed off — must not re-emit.
        await orch._maybe_handoff_board_review_to_ceo(task_id)

    task_svc.mark_board_review_complete.assert_awaited_once()
    svc.send_board_review_complete_notification.assert_awaited_once_with(
        task_id=task_id
    )
    assert task_id in orch._board_review_ceo_notified


@pytest.mark.asyncio
async def test_ceo_not_handed_off_while_review_incomplete() -> None:
    """No flag and no CEO notification until BOTH reviewers are done."""
    orch = _make_orch()
    task_id = str(uuid4())
    # Only PO has been dispatched/finished.
    orch._board_dispatched.add(("product-owner", task_id))

    svc = AsyncMock()
    task_svc = AsyncMock()
    db_ctx, task_ctx = _patch_handoff_db(task_svc)
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch("roboco.services.notification.NotificationService", return_value=svc),
        db_ctx,
        task_ctx,
    ):
        await orch._maybe_handoff_board_review_to_ceo(task_id)

    task_svc.mark_board_review_complete.assert_not_awaited()
    svc.send_board_review_complete_notification.assert_not_awaited()
    assert task_id not in orch._board_review_ceo_notified


@pytest.mark.asyncio
async def test_ceo_handoff_failure_allows_retry() -> None:
    """A handoff failure clears the one-shot guard so a later tick retries."""
    orch = _make_orch()
    task_id = str(uuid4())
    orch._board_dispatched.add(("product-owner", task_id))
    orch._board_dispatched.add(("head-marketing", task_id))

    svc = AsyncMock()
    svc.send_board_review_complete_notification.side_effect = RuntimeError("db down")
    task_svc = AsyncMock()
    db_ctx, task_ctx = _patch_handoff_db(task_svc)
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch("roboco.services.notification.NotificationService", return_value=svc),
        db_ctx,
        task_ctx,
    ):
        await orch._maybe_handoff_board_review_to_ceo(task_id)

    # Guard cleared so a later, healthy tick can re-run the handoff.
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


# ---------------------------------------------------------------------------
# _inject_board_brief_into_parked_intake: single-task vs. MegaTask umbrella
# composer choice (the keep-alive re-draft loop's message content).
# ---------------------------------------------------------------------------


class _FakeParkedSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class _FakeLiveRegistry:
    """Stands in for the live-session registry: one parked session, records
    every ``deliver`` call so the test can inspect the composed message."""

    def __init__(self, session: _FakeParkedSession | None) -> None:
        self._session = session
        self.delivered: list[tuple[str, str]] = []

    def find_by_task(self, _task_id: str) -> _FakeParkedSession | None:
        return self._session

    async def deliver(self, session_id: str, message: str) -> bool:
        self.delivered.append((session_id, message))
        return True


def _patch_inject_seams(
    task: Any, journal_entries: list[dict[str, Any]], children: list[Any] | None = None
) -> tuple[Any, ...]:
    """Patch the DB/task/journal seams ``_inject_board_brief_into_parked_intake``
    opens, mirroring ``_patch_handoff_db``'s shape for this function's own
    (different) import points."""

    @asynccontextmanager
    async def _fake_ctx() -> AsyncIterator[Any]:
        yield AsyncMock()

    task_svc = AsyncMock()
    task_svc.get = AsyncMock(return_value=task)
    task_svc.get_live_subtasks = AsyncMock(return_value=children or [])
    journal_svc = AsyncMock()
    journal_svc.board_review_brief = AsyncMock(return_value=journal_entries)
    return (
        patch("roboco.db.base.get_db_context", _fake_ctx),
        patch("roboco.services.task.get_task_service", return_value=task_svc),
        patch("roboco.services.journal.get_journal_service", return_value=journal_svc),
    )


@pytest.mark.asyncio
async def test_inject_board_brief_single_task_uses_single_composer() -> None:
    """A normal (non-batch) parked task's redraft message uses
    ``compose_redraft_message`` — the umbrella branch must not change it."""
    orch = _make_orch()
    task_id = str(uuid4())
    registry = _FakeLiveRegistry(_FakeParkedSession("live-session-1"))

    task = SimpleNamespace(
        title="Original task",
        description="Original description.",
        acceptance_criteria=["do the thing"],
        batch_id=None,
        parent_task_id=None,
    )
    db_ctx, task_ctx, journal_ctx = _patch_inject_seams(task, [])
    with (
        patch("roboco.services.prompter_live.get_live_registry", return_value=registry),
        db_ctx,
        task_ctx,
        journal_ctx,
    ):
        await orch._inject_board_brief_into_parked_intake(task_id)

    assert len(registry.delivered) == 1
    _sid, message = registry.delivered[0]
    assert "Original task" in message
    assert "revising an existing task draft" in message
    assert "propose_batch" not in message


@pytest.mark.asyncio
async def test_inject_board_brief_batch_umbrella_uses_batch_composer() -> None:
    """A parked MegaTask umbrella's redraft message uses
    ``compose_batch_redraft_message``: every root-subtask's snapshot plus the
    ``propose_batch`` re-submit instruction."""
    orch = _make_orch()
    task_id = str(uuid4())
    registry = _FakeLiveRegistry(_FakeParkedSession("live-session-2"))

    umbrella = SimpleNamespace(
        title="MegaTask: Ship things",
        description="Coordinate 2 sequenced tasks as one MegaTask.",
        batch_id=uuid4(),
        parent_task_id=None,
    )
    children = [
        SimpleNamespace(
            title="Backend piece",
            description="Backend description.",
            acceptance_criteria=["backend ac"],
            project=SimpleNamespace(name="roboco-api"),
            cell_projects=[],
        ),
    ]
    db_ctx, task_ctx, journal_ctx = _patch_inject_seams(umbrella, [], children)
    with (
        patch("roboco.services.prompter_live.get_live_registry", return_value=registry),
        db_ctx,
        task_ctx,
        journal_ctx,
    ):
        await orch._inject_board_brief_into_parked_intake(task_id)

    assert len(registry.delivered) == 1
    _sid, message = registry.delivered[0]
    assert "Ship things" in message
    assert "Backend piece" in message
    assert "roboco-api" in message
    assert "propose_batch" in message


@pytest.mark.asyncio
async def test_inject_board_brief_no_parked_session_is_noop() -> None:
    """No session parked for the task → no-op, never raises."""
    orch = _make_orch()
    registry = _FakeLiveRegistry(None)
    with patch(
        "roboco.services.prompter_live.get_live_registry", return_value=registry
    ):
        await orch._inject_board_brief_into_parked_intake(str(uuid4()))
    assert registry.delivered == []

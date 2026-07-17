"""Terminal worktree cleanup on complete/ceo_approve (F123 followup).

Completed/merged tasks must not leak their per-task worktree on disk until the
whole agent is deleted. The two terminal→completed paths (cell-PM ``complete``
after the leaf PR merges; CEO ``ceo_approve`` after root→master merges) remove
the assignee's worktree best-effort. Removal is terminal-only — a dev task
bounces ``needs_revision`` off the earlier review states and needs its worktree
back, so cleanup fires only at ``completed`` (post-merge, branch truly done).
No-op for branchless tasks (no worktree was ever cut). Best-effort: a removal
failure never blocks completion.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.services.task import TaskService


def _build_task(**overrides: object) -> MagicMock:
    base: dict[str, object] = {
        "id": uuid4(),
        "status": TaskStatus.PENDING,
        "branch_name": "feature/backend/abc12345",
        "work_session_id": None,
        "assigned_to": None,
    }
    base.update(overrides)
    return MagicMock(**base)


def _bind(svc: TaskService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _slug_row(slug: str) -> MagicMock:
    return MagicMock(scalar_one_or_none=MagicMock(return_value=slug))


def _svc(execute: object) -> tuple[TaskService, MagicMock]:
    # Build the session as a local MagicMock and preset `execute` on it before
    # handing it to TaskService — assigning to `svc.session.execute` directly
    # trips mypy's method-assign (session is typed as a real AsyncSession).
    session = MagicMock()
    session.execute = execute
    session.flush = AsyncMock()
    return TaskService(session), session


# ---------------------------------------------------------------------------
# complete (cell PM, awaiting_pm_review -> completed, after leaf PR merge)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_removes_assignee_worktree_best_effort() -> None:
    task = _build_task(status=TaskStatus.AWAITING_PM_REVIEW)
    svc, _ = _svc(AsyncMock(return_value=_slug_row("roboco-api")))
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_get_completing_agent_role", AsyncMock(return_value="cell_pm"))
    _bind(svc, "_validate_completion_prerequisites", AsyncMock(return_value=[]))
    _bind(svc, "_apply_complete_approval_chain", AsyncMock(return_value=None))
    _bind(svc, "_cancelled_force_allowed", MagicMock(return_value=True))
    _bind(svc, "_assert_pr_merged_for_complete", AsyncMock(return_value=True))
    _bind(svc, "_validate_and_set_status", MagicMock())
    _bind(svc, "_close_work_session_for_task", AsyncMock())
    _bind(svc, "_trigger_completion_hooks", AsyncMock())
    _bind(svc, "_unblock_dependents", AsyncMock())
    remove = AsyncMock()
    _bind(svc, "_remove_task_worktree_best_effort", remove)

    result = await svc.complete(task.id)

    assert result is task
    remove.assert_awaited_once_with(task, "roboco-api", force_branch_delete=False)


@pytest.mark.asyncio
async def test_complete_skips_worktree_cleanup_for_branchless_task() -> None:
    # A branchless/umbrella task had no worktree cut — removal must be a no-op
    # (and must not even probe the project slug).
    task = _build_task(status=TaskStatus.AWAITING_PM_REVIEW, branch_name=None)
    execute = AsyncMock()
    svc, session = _svc(execute)
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_get_completing_agent_role", AsyncMock(return_value="cell_pm"))
    _bind(svc, "_validate_completion_prerequisites", AsyncMock(return_value=[]))
    _bind(svc, "_apply_complete_approval_chain", AsyncMock(return_value=None))
    _bind(svc, "_cancelled_force_allowed", MagicMock(return_value=True))
    _bind(svc, "_assert_pr_merged_for_complete", AsyncMock(return_value=True))
    _bind(svc, "_validate_and_set_status", MagicMock())
    _bind(svc, "_close_work_session_for_task", AsyncMock())
    _bind(svc, "_trigger_completion_hooks", AsyncMock())
    _bind(svc, "_unblock_dependents", AsyncMock())
    remove = AsyncMock()
    _bind(svc, "_remove_task_worktree_best_effort", remove)

    result = await svc.complete(task.id)

    assert result is task
    remove.assert_not_awaited()
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_not_blocked_by_worktree_removal_failure() -> None:
    # Best-effort: a git/FS failure during cleanup must NOT fail the completion.
    task = _build_task(status=TaskStatus.AWAITING_PM_REVIEW)
    svc, _ = _svc(AsyncMock(return_value=_slug_row("roboco-api")))
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_get_completing_agent_role", AsyncMock(return_value="cell_pm"))
    _bind(svc, "_validate_completion_prerequisites", AsyncMock(return_value=[]))
    _bind(svc, "_apply_complete_approval_chain", AsyncMock(return_value=None))
    _bind(svc, "_cancelled_force_allowed", MagicMock(return_value=True))
    _bind(svc, "_assert_pr_merged_for_complete", AsyncMock(return_value=True))
    _bind(svc, "_validate_and_set_status", MagicMock())
    _bind(svc, "_close_work_session_for_task", AsyncMock())
    _bind(svc, "_trigger_completion_hooks", AsyncMock())
    _bind(svc, "_unblock_dependents", AsyncMock())
    remove = AsyncMock(side_effect=RuntimeError("git worktree remove failed"))
    _bind(svc, "_remove_task_worktree_best_effort", remove)

    result = await svc.complete(task.id)

    assert result is task  # completion still succeeds


# ---------------------------------------------------------------------------
# ceo_approve (CEO, awaiting_ceo_approval -> completed, after root->master merge)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ceo_approve_removes_assignee_worktree_best_effort() -> None:
    task = _build_task(status=TaskStatus.AWAITING_CEO_APPROVAL, work_session_id=None)
    svc, _ = _svc(AsyncMock(return_value=_slug_row("roboco-api")))
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_validate_and_set_status", MagicMock())
    _bind(svc, "_close_work_session_for_task", AsyncMock())
    _bind(svc, "_trigger_completion_hooks", AsyncMock())
    _bind(svc, "_unblock_dependents", AsyncMock())
    _bind(svc, "_emit_task_event", AsyncMock())
    remove = AsyncMock()
    _bind(svc, "_remove_task_worktree_best_effort", remove)

    result = await svc.ceo_approve(task.id)

    assert result is task
    remove.assert_awaited_once_with(task, "roboco-api", force_branch_delete=False)


@pytest.mark.asyncio
async def test_ceo_approve_skips_worktree_cleanup_for_branchless_task() -> None:
    task = _build_task(status=TaskStatus.AWAITING_CEO_APPROVAL, branch_name=None)
    svc, _ = _svc(AsyncMock())
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_validate_and_set_status", MagicMock())
    _bind(svc, "_close_work_session_for_task", AsyncMock())
    _bind(svc, "_trigger_completion_hooks", AsyncMock())
    _bind(svc, "_unblock_dependents", AsyncMock())
    _bind(svc, "_emit_task_event", AsyncMock())
    remove = AsyncMock()
    _bind(svc, "_remove_task_worktree_best_effort", remove)

    result = await svc.ceo_approve(task.id)

    assert result is task
    remove.assert_not_awaited()


@pytest.mark.asyncio
async def test_ceo_approve_closes_work_session_and_triggers_completion_hooks() -> None:
    """#21/#98: ceo_approve must close the work session and run the full
    completion-hook fan-out (commits + dev_notes indexing), mirroring
    ``complete()`` — not hand-roll a lone learnings task and leave the work
    session ACTIVE forever. The PR-merged gate already ran above."""
    task = _build_task(status=TaskStatus.AWAITING_CEO_APPROVAL, work_session_id=None)
    svc, _ = _svc(AsyncMock(return_value=_slug_row("roboco-api")))
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_validate_and_set_status", MagicMock())
    close_ws = AsyncMock()
    hooks = AsyncMock()
    _bind(svc, "_close_work_session_for_task", close_ws)
    _bind(svc, "_trigger_completion_hooks", hooks)
    _bind(svc, "_unblock_dependents", AsyncMock())
    _bind(svc, "_emit_task_event", AsyncMock())
    _bind(svc, "_remove_task_worktree_best_effort", AsyncMock())

    result = await svc.ceo_approve(task.id)

    assert result is task
    close_ws.assert_awaited_once()
    # The hooks fire with the CEO actor (None) — same as complete() for a
    # non-agent completion path.
    hooks.assert_awaited_once_with(task, None)


# ---------------------------------------------------------------------------
# #216: recurring FS/permission cleanup failure escalates to the CEO
# ---------------------------------------------------------------------------


def _reset_cleanup_streak() -> None:
    TaskService._worktree_cleanup_fail_streak = 0
    TaskService._worktree_cleanup_escalated = False


@pytest.mark.asyncio
async def test_recurring_worktree_cleanup_failure_escalates_to_ceo() -> None:
    """#216: a recurring FS/permission failure (stuck mount, perms) used to
    leak worktrees indefinitely with only a warning log. After N consecutive
    OSError failures a CEO alert fires once; further failures are suppressed
    until a success resets the streak."""
    _reset_cleanup_streak()
    n = TaskService._WORKTREE_CLEANUP_ESCALATE_AFTER
    task = _build_task(status=TaskStatus.COMPLETED)
    svc, _ = _svc(AsyncMock(return_value=_slug_row("roboco-api")))
    _bind(
        svc,
        "_remove_task_worktree_best_effort",
        AsyncMock(side_effect=PermissionError("denied")),
    )

    with patch(
        "roboco.services.notification.NotificationService.send_ack_notification",
        new=AsyncMock(),
    ) as notifier:
        for _ in range(n - 1):
            await svc._remove_task_worktree_on_terminal(task)
        notifier.assert_not_awaited()
        await svc._remove_task_worktree_on_terminal(task)  # Nth failure
        notifier.assert_awaited_once()
        call = notifier.await_args
        assert call is not None
        assert call.kwargs["to_agent"] == "ceo"
        assert str(task.id) in str(call.kwargs.get("task_id") or call.args)
        # Suppressed on the (N+1)th — one escalation per failure streak.
        await svc._remove_task_worktree_on_terminal(task)
        notifier.assert_awaited_once()

    _reset_cleanup_streak()


@pytest.mark.asyncio
async def test_worktree_cleanup_success_resets_failure_streak() -> None:
    """A successful cleanup resets the streak + escalation flag, so a later
    failure streak must re-accumulate to the full threshold before escalating."""
    _reset_cleanup_streak()
    n = TaskService._WORKTREE_CLEANUP_ESCALATE_AFTER
    task = _build_task(status=TaskStatus.COMPLETED)
    svc, _ = _svc(AsyncMock(return_value=_slug_row("roboco-api")))
    remove = AsyncMock(side_effect=PermissionError("denied"))
    _bind(svc, "_remove_task_worktree_best_effort", remove)

    with patch(
        "roboco.services.notification.NotificationService.send_ack_notification",
        new=AsyncMock(),
    ) as notifier:
        for _ in range(n - 1):
            await svc._remove_task_worktree_on_terminal(task)
        notifier.assert_not_awaited()
        # A success resets the streak.
        remove.side_effect = None
        await svc._remove_task_worktree_on_terminal(task)
        # Re-introduce failures: needs a full N again, not 1.
        remove.side_effect = PermissionError("denied")
        for _ in range(n - 1):
            await svc._remove_task_worktree_on_terminal(task)
        notifier.assert_not_awaited()
        await svc._remove_task_worktree_on_terminal(task)  # Nth of new streak
        notifier.assert_awaited_once()

    _reset_cleanup_streak()


@pytest.mark.asyncio
async def test_non_fs_worktree_cleanup_failure_does_not_escalate() -> None:
    """A non-FS failure (git RuntimeError, DB error) is task-specific, not the
    systemic operator-actionable FS case — it logs but never escalates, and it
    resets the streak so an interleaved FS streak can't ride on it."""
    _reset_cleanup_streak()
    n = TaskService._WORKTREE_CLEANUP_ESCALATE_AFTER
    task = _build_task(status=TaskStatus.COMPLETED)
    svc, _ = _svc(AsyncMock(return_value=_slug_row("roboco-api")))
    _bind(
        svc,
        "_remove_task_worktree_best_effort",
        AsyncMock(side_effect=RuntimeError("git worktree remove failed")),
    )

    with patch(
        "roboco.services.notification.NotificationService.send_ack_notification",
        new=AsyncMock(),
    ) as notifier:
        for _ in range(n + 2):
            await svc._remove_task_worktree_on_terminal(task)
        notifier.assert_not_awaited()

    _reset_cleanup_streak()

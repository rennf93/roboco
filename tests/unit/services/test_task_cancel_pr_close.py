"""Cancel closes the task's own open PR (GAP A).

``_delete_task_branch_best_effort`` already force-deletes the task's remote
branch + worktree on cancel, but an open PR (``task.pr_number`` set) was left
open on the forge forever — nothing ever closed it. This is the isolated
unit test for the new ``_close_task_pr_best_effort`` best-effort chokepoint;
``tests/integration/test_task_service_lifecycle_misc.py`` covers the full
``cancel()`` wiring against a real DB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.services.task import TaskService


def _service() -> TaskService:
    svc = TaskService.__new__(TaskService)
    svc.log = MagicMock()
    svc.session = MagicMock()
    return svc


def _session(slug: str | None) -> MagicMock:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = slug
    session.execute = AsyncMock(return_value=result)
    return session


def _task(*, pr_number: int | None) -> MagicMock:
    return MagicMock(id=uuid4(), project_id=uuid4(), pr_number=pr_number)


@pytest.mark.asyncio
async def test_closes_open_pr_via_git_service() -> None:
    svc = _service()
    task = _task(pr_number=42)
    svc.session = _session("roboco-api")

    git_service = MagicMock()
    git_service.close_task_pr_best_effort = AsyncMock()

    with patch(
        "roboco.services.git.get_git_service",
        MagicMock(return_value=git_service),
    ):
        await svc._close_task_pr_best_effort(task)

    git_service.close_task_pr_best_effort.assert_awaited_once_with("roboco-api", 42)


@pytest.mark.asyncio
async def test_skips_when_no_pr_number() -> None:
    # Task never opened a PR (or was cancelled before claim) — nothing to
    # close, and no project lookup should even fire.
    svc = _service()
    task = _task(pr_number=None)
    svc.session = _session("roboco-api")

    git_service = MagicMock()
    git_service.close_task_pr_best_effort = AsyncMock()

    with patch(
        "roboco.services.git.get_git_service",
        MagicMock(return_value=git_service),
    ):
        await svc._close_task_pr_best_effort(task)

    git_service.close_task_pr_best_effort.assert_not_awaited()
    svc.session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_project_slug_unresolvable() -> None:
    svc = _service()
    task = _task(pr_number=42)
    svc.session = _session(None)

    git_service = MagicMock()
    git_service.close_task_pr_best_effort = AsyncMock()

    with patch(
        "roboco.services.git.get_git_service",
        MagicMock(return_value=git_service),
    ):
        await svc._close_task_pr_best_effort(task)

    git_service.close_task_pr_best_effort.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_close_failure_does_not_raise() -> None:
    # Best-effort: a forge failure logs and never blocks the cancellation.
    svc = _service()
    task = _task(pr_number=42)
    svc.session = _session("roboco-api")

    git_service = MagicMock()
    git_service.close_task_pr_best_effort = AsyncMock(
        side_effect=RuntimeError("forge unreachable")
    )

    with patch(
        "roboco.services.git.get_git_service",
        MagicMock(return_value=git_service),
    ):
        await svc._close_task_pr_best_effort(task)  # must not raise

    svc.log.warning.assert_called_once()

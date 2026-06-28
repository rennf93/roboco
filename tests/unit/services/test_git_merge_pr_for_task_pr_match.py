"""F050: merge_pr_for_task must not merge a caller-provided pr_number that
doesn't match the task's recorded PR.

``GitMergePRRequest.pr_number`` is caller-provided. When a ``task_id`` is
present the service knows the task's *own* recorded PR (``task.pr_number``),
set when the PR was opened. Without a match check a caller (a buggy client, a
stale panel form, an agent that cached an old PR number) can ask the CEO/PM
merge path to merge PR #N for task T whose recorded PR is #M — merging the
wrong PR against the wrong task's work-session and auto-complete. The recorded
PR is the source of truth; the caller's number must agree with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.api.schemas.git import GitMergePRRequest
from roboco.models.base import AgentRole
from roboco.services import git as git_module
from roboco.services.base import ValidationError
from roboco.services.git import GitService

_RECORDED_PR = 42  # the task's own recorded PR number (source of truth)
_CALLER_PR = 7  # a stale/wrong caller-provided number that must not redirect the merge


def _git_service() -> GitService:
    svc = GitService.__new__(GitService)
    svc.session = AsyncMock()
    return svc


def _task(*, pr_number: int | None, project_id: Any | None = None) -> Any:
    task = MagicMock()
    task.pr_number = pr_number
    task.status = "awaiting_pm_review"
    task.project_id = project_id
    task.work_session_id = None
    return task


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    svc: GitService,
    task: Any,
) -> tuple[AsyncMock, AsyncMock]:
    """Wire merge_pr_for_task's dependencies to mocks; return (merge, ws)."""
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    monkeypatch.setattr(git_module, "get_task_service", lambda _s: task_svc)

    ws_svc = AsyncMock()
    monkeypatch.setattr(git_module, "get_work_session_service", lambda _s: ws_svc)

    # Role gate passes; status already a str.
    monkeypatch.setattr(svc, "_assert_merge_role", MagicMock(return_value=None))
    # Task has a project, so _project_for_task is never reached.
    monkeypatch.setattr(svc, "get_workspace", AsyncMock(return_value=Path("/ws")))
    merge = AsyncMock(return_value=("master", "abc123"))
    monkeypatch.setattr(svc, "merge_pull_request", merge)
    monkeypatch.setattr(svc, "_auto_complete_on_merge", AsyncMock(return_value=None))
    return merge, ws_svc


@pytest.mark.asyncio
async def test_mismatched_pr_number_rejected_before_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task's recorded PR is #42; the caller asks to merge #7. The merge
    must NOT proceed — the recorded PR is the source of truth, not the caller's
    number (which may be stale/wrong and would merge the wrong PR for this
    task)."""
    svc = _git_service()
    task = _task(pr_number=_RECORDED_PR, project_id=uuid4())
    merge, _ws = _wire(monkeypatch, svc, task)

    data = GitMergePRRequest(
        project_slug="roboco",
        pr_number=_CALLER_PR,
        task_id=uuid4(),
        merge_method="squash",
    )

    with pytest.raises(ValidationError):
        await svc.merge_pr_for_task(
            agent_id=uuid4(), agent_role=AgentRole.CEO, data=data
        )

    merge.assert_not_awaited()
    svc.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_matching_pr_number_proceeds_to_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller's number equals the recorded PR — merge proceeds with the
    recorded number (so even a stale client number can't redirect the merge)."""
    svc = _git_service()
    task = _task(pr_number=_RECORDED_PR, project_id=uuid4())
    merge, _ws = _wire(monkeypatch, svc, task)

    data = GitMergePRRequest(
        project_slug="roboco",
        pr_number=_RECORDED_PR,
        task_id=uuid4(),
        merge_method="squash",
    )

    target, commit = await svc.merge_pr_for_task(
        agent_id=uuid4(), agent_role=AgentRole.CELL_PM, data=data
    )

    assert target == "master"
    assert commit == "abc123"
    merge.assert_awaited_once()
    # The merge runs against the recorded PR number, not a re-derived one —
    # but the call's pr_number must equal the recorded one (here they match).
    assert merge.call_args.kwargs["pr_number"] == _RECORDED_PR


@pytest.mark.asyncio
async def test_task_with_no_recorded_pr_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task with no recorded pr_number has nothing to verify the caller's
    number against — merging "for" it is unverifiable and must be refused,
    not fall through to the caller-provided number (which would re-open the
    wrong-PR gap for any task that lost its pr_number)."""
    svc = _git_service()
    task = _task(pr_number=None, project_id=uuid4())
    merge, _ws = _wire(monkeypatch, svc, task)

    data = GitMergePRRequest(
        project_slug="roboco",
        pr_number=_CALLER_PR,
        task_id=uuid4(),
        merge_method="squash",
    )

    with pytest.raises(ValidationError):
        await svc.merge_pr_for_task(
            agent_id=uuid4(), agent_role=AgentRole.CEO, data=data
        )

    merge.assert_not_awaited()

"""Tests for ContentActions.pr_update — gateway verb behavior matrix.

Covers: missing pr_number, all-None fields, non-assignee non-PM rejection,
per-field forwarding, and GitError surfacing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from roboco.exceptions import GitError
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


def _make_deps(**overrides: AsyncMock) -> ContentActionsDeps:
    """Wire ContentActionsDeps with mocks; honour caller overrides."""
    task = overrides.get("task", AsyncMock())
    git = overrides.get("git", AsyncMock())
    return ContentActionsDeps(
        task=task,
        git=git,
        a2a=overrides.get("a2a", AsyncMock()),
        journal=overrides.get("journal", AsyncMock()),
        workspace=overrides.get("workspace", AsyncMock()),
        notifications=overrides.get("notifications", AsyncMock()),
        notification_delivery=overrides.get("notification_delivery", AsyncMock()),
    )


def _task(
    *,
    pr_number: int | None = 7,
    assigned_to: UUID,
    team: str = "backend",
) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        status="in_progress",
        pr_number=pr_number,
        pr_url=(
            f"https://github.com/acme/repo/pull/{pr_number}"
            if pr_number is not None
            else None
        ),
        assigned_to=assigned_to,
        team=team,
    )


def _agent(role: str, team: str | None = None) -> MagicMock:
    return MagicMock(id=uuid4(), role=role, team=team)


@pytest.mark.asyncio
async def test_pr_update_missing_pr_number_returns_invalid_state() -> None:
    """task.pr_number is None → invalid_state with remediate 'call open_pr'."""
    agent_id = uuid4()
    task = _task(pr_number=None, assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = _agent("developer", "backend")

    deps = _make_deps(task=task_svc)
    ca = ContentActions(deps)

    env = await ca.pr_update(
        agent_id=agent_id,
        task_id=task.id,
        title="updated PR title",
        body=None,
        reviewers=None,
    )
    body = env.as_dict()

    assert body["error"] == "invalid_state"
    assert "open_pr" in body["remediate"]
    deps.git.update_pr_for_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_update_all_fields_none_returns_invalid_state() -> None:
    """All of title/body/reviewers None → invalid_state."""
    agent_id = uuid4()
    task = _task(assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = _agent("developer", "backend")

    deps = _make_deps(task=task_svc)
    ca = ContentActions(deps)

    env = await ca.pr_update(
        agent_id=agent_id, task_id=task.id, title=None, body=None, reviewers=None
    )
    body = env.as_dict()

    assert body["error"] == "invalid_state"
    assert "at least one" in body["remediate"]
    deps.git.update_pr_for_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_update_non_assignee_non_pm_returns_not_authorized() -> None:
    """A developer who is neither the assignee nor a PM → not_authorized."""
    assignee_id = uuid4()
    caller_id = uuid4()
    task = _task(assigned_to=assignee_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = _agent("developer", "backend")

    deps = _make_deps(task=task_svc)
    ca = ContentActions(deps)

    env = await ca.pr_update(
        agent_id=caller_id,
        task_id=task.id,
        title="new title",
        body=None,
        reviewers=None,
    )
    body = env.as_dict()

    assert body["error"] == "not_authorized"
    deps.git.update_pr_for_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_update_assignee_title_only_forwarded() -> None:
    """Assignee + title only forwards (title=X, body=None, reviewers=None)."""
    agent_id = uuid4()
    task = _task(assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = _agent("developer", "backend")

    git_svc = AsyncMock()
    git_svc.update_pr_for_task.return_value = {
        "pr_number": 7,
        "pr_url": task.pr_url,
        "updated_fields": ["title"],
    }
    deps = _make_deps(task=task_svc, git=git_svc)
    ca = ContentActions(deps)

    env = await ca.pr_update(
        agent_id=agent_id,
        task_id=task.id,
        title="new title",
        body=None,
        reviewers=None,
    )
    body = env.as_dict()

    assert body["error"] is None
    assert body["evidence"]["updated_fields"] == ["title"]
    git_svc.update_pr_for_task.assert_awaited_once()
    call_kwargs = git_svc.update_pr_for_task.call_args.kwargs
    assert call_kwargs["title"] == "new title"
    assert call_kwargs["body"] is None
    assert call_kwargs["reviewers"] is None


@pytest.mark.asyncio
async def test_pr_update_reviewers_only_forwarded() -> None:
    """Assignee + reviewers only → forwarded as the only non-None arg."""
    agent_id = uuid4()
    task = _task(assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = _agent("developer", "backend")

    git_svc = AsyncMock()
    git_svc.update_pr_for_task.return_value = {
        "pr_number": 7,
        "pr_url": task.pr_url,
        "updated_fields": ["reviewers"],
    }
    deps = _make_deps(task=task_svc, git=git_svc)
    ca = ContentActions(deps)

    env = await ca.pr_update(
        agent_id=agent_id,
        task_id=task.id,
        title=None,
        body=None,
        reviewers=["be-dev-2"],
    )
    body = env.as_dict()

    assert body["error"] is None
    git_svc.update_pr_for_task.assert_awaited_once()
    call_kwargs = git_svc.update_pr_for_task.call_args.kwargs
    assert call_kwargs["title"] is None
    assert call_kwargs["body"] is None
    assert call_kwargs["reviewers"] == ["be-dev-2"]


@pytest.mark.asyncio
async def test_pr_update_all_three_forwarded() -> None:
    """All three fields → all forwarded; updated_fields reflects all three."""
    agent_id = uuid4()
    task = _task(assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = _agent("developer", "backend")

    git_svc = AsyncMock()
    git_svc.update_pr_for_task.return_value = {
        "pr_number": 7,
        "pr_url": task.pr_url,
        "updated_fields": ["title", "body", "reviewers"],
    }
    deps = _make_deps(task=task_svc, git=git_svc)
    ca = ContentActions(deps)

    env = await ca.pr_update(
        agent_id=agent_id,
        task_id=task.id,
        title="updated PR title",
        body="a substantive PR body",
        reviewers=["be-dev-2"],
    )
    body = env.as_dict()

    assert body["error"] is None
    call_kwargs = git_svc.update_pr_for_task.call_args.kwargs
    assert call_kwargs["title"] == "updated PR title"
    assert call_kwargs["body"] == "a substantive PR body"
    assert call_kwargs["reviewers"] == ["be-dev-2"]
    assert set(body["evidence"]["updated_fields"]) == {"title", "body", "reviewers"}


@pytest.mark.asyncio
async def test_pr_update_cell_pm_on_same_team_allowed() -> None:
    """Cell PM whose team == task.team can update the PR (PM authority)."""
    pm_id = uuid4()
    assignee_id = uuid4()
    task = _task(assigned_to=assignee_id, team="backend")
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = _agent("cell_pm", "backend")

    git_svc = AsyncMock()
    git_svc.update_pr_for_task.return_value = {
        "pr_number": 7,
        "pr_url": task.pr_url,
        "updated_fields": ["title"],
    }
    deps = _make_deps(task=task_svc, git=git_svc)
    ca = ContentActions(deps)

    env = await ca.pr_update(
        agent_id=pm_id,
        task_id=task.id,
        title="updated PR title",
        body=None,
        reviewers=None,
    )
    body = env.as_dict()

    assert body["error"] is None
    git_svc.update_pr_for_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_pr_update_cell_pm_on_other_team_rejected() -> None:
    """Cell PM on a different team than the task → not_authorized."""
    pm_id = uuid4()
    assignee_id = uuid4()
    task = _task(assigned_to=assignee_id, team="backend")
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = _agent("cell_pm", "frontend")

    deps = _make_deps(task=task_svc)
    ca = ContentActions(deps)

    env = await ca.pr_update(
        agent_id=pm_id,
        task_id=task.id,
        title="updated PR title",
        body=None,
        reviewers=None,
    )

    assert env.as_dict()["error"] == "not_authorized"
    deps.git.update_pr_for_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_update_main_pm_any_team_allowed() -> None:
    """Main PM is cross-team and may update any task's PR."""
    pm_id = uuid4()
    assignee_id = uuid4()
    task = _task(assigned_to=assignee_id, team="backend")
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = _agent("main_pm", team=None)

    git_svc = AsyncMock()
    git_svc.update_pr_for_task.return_value = {
        "pr_number": 7,
        "pr_url": task.pr_url,
        "updated_fields": ["title"],
    }
    deps = _make_deps(task=task_svc, git=git_svc)
    ca = ContentActions(deps)

    env = await ca.pr_update(
        agent_id=pm_id,
        task_id=task.id,
        title="updated PR title",
        body=None,
        reviewers=None,
    )

    assert env.as_dict()["error"] is None
    git_svc.update_pr_for_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_pr_update_task_not_found_returns_not_found() -> None:
    """Unknown task_id → not_found envelope; git layer never invoked."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    ca = ContentActions(deps)

    env = await ca.pr_update(
        agent_id=agent_id,
        task_id=uuid4(),
        title="updated PR title",
        body=None,
        reviewers=None,
    )
    body = env.as_dict()

    assert body["error"] == "not_found"
    deps.git.update_pr_for_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_update_git_error_returned_as_invalid_state() -> None:
    """GitService raises GitError → mapped to invalid_state envelope with detail."""
    agent_id = uuid4()
    task = _task(assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = _agent("developer", "backend")

    git_svc = AsyncMock()
    git_svc.update_pr_for_task.side_effect = GitError("PR not found: #7 on acme/repo")
    deps = _make_deps(task=task_svc, git=git_svc)
    ca = ContentActions(deps)

    env = await ca.pr_update(
        agent_id=agent_id,
        task_id=task.id,
        title="updated PR title",
        body=None,
        reviewers=None,
    )
    body = env.as_dict()

    assert body["error"] == "invalid_state"
    assert "PR not found" in body["message"]

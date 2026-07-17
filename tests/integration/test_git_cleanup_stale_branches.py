"""GitService.cleanup_stale_branches — the branch-cleanup sweep's selection
+ per-branch delete logic (backs the panel's "Clean up stale branches"
button / POST /git/branches/cleanup).

Real DB (ProjectTable/TaskTable/AgentTable) so the terminal-only + env-ladder
selection runs for real; the two external-I/O boundaries are mocked so the
test never makes a real GitHub API call or runs a real git subprocess:
``_delete_remote_branch_best_effort`` (network) and
``roboco.services.git.get_workspace_service`` (local clone/subprocess).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.git import GitService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def cleanup_setup(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict[str, Any]]:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:8]}",
        git_url="https://github.com/acme/repo.git",
        default_branch="master",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()

    svc = GitService(db_session)
    monkeypatch.setattr(svc, "_token_for_project", AsyncMock(return_value="tok"))
    # Remote delete: no real HTTP — a bare True/False signal is all
    # cleanup_stale_branches consumes from it.
    monkeypatch.setattr(
        svc, "_delete_remote_branch_best_effort", AsyncMock(return_value=True)
    )
    ws_svc = MagicMock()
    ws_svc.get_clone_root_path = MagicMock(
        return_value=f"/data/workspaces/{project.slug}/backend/{agent.slug}"
    )
    ws_svc.delete_local_branch = AsyncMock()
    monkeypatch.setattr("roboco.services.git.get_workspace_service", lambda _s: ws_svc)

    yield {
        "svc": svc,
        "db": db_session,
        "agent": agent,
        "project": project,
        "ws_svc": ws_svc,
    }


def _task(
    setup: dict[str, Any],
    *,
    branch: str,
    status: TaskStatus,
    assigned: bool = True,
) -> TaskTable:
    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        status=status,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        team=Team.BACKEND,
        project_id=setup["project"].id,
        created_by=setup["agent"].id,
        assigned_to=setup["agent"].id if assigned else None,
        acceptance_criteria=["ac"],
        branch_name=branch,
    )
    setup["db"].add(task)
    return task


@pytest.mark.asyncio
async def test_only_terminal_tasks_are_candidates(
    cleanup_setup: dict[str, Any],
) -> None:
    _task(cleanup_setup, branch="feature/backend/pending", status=TaskStatus.PENDING)
    _task(
        cleanup_setup,
        branch="feature/backend/inprogress",
        status=TaskStatus.IN_PROGRESS,
    )
    _task(
        cleanup_setup, branch="feature/backend/completed", status=TaskStatus.COMPLETED
    )
    _task(
        cleanup_setup, branch="feature/backend/cancelled", status=TaskStatus.CANCELLED
    )
    await cleanup_setup["db"].flush()

    result = await cleanup_setup["svc"].cleanup_stale_branches(
        cleanup_setup["project"].slug
    )

    # Only the 2 terminal tasks are candidates — pending/in_progress untouched.
    assert result == (2, 2, 0, 0, False)


@pytest.mark.asyncio
async def test_env_ladder_branch_is_excluded(cleanup_setup: dict[str, Any]) -> None:
    project = cleanup_setup["project"]
    project.environments = [
        {"name": "head", "branch": "develop"},
        {"name": "prod", "branch": "master"},
    ]
    await cleanup_setup["db"].flush()

    # A terminal task whose branch happens to equal a ladder rung must never
    # be touched — the ladder outlives any one task.
    _task(cleanup_setup, branch="develop", status=TaskStatus.COMPLETED)
    _task(
        cleanup_setup, branch="feature/backend/real-task", status=TaskStatus.COMPLETED
    )
    await cleanup_setup["db"].flush()

    result = await cleanup_setup["svc"].cleanup_stale_branches(project.slug)

    assert result == (1, 1, 0, 0, False)
    cleanup_setup["ws_svc"].delete_local_branch.assert_awaited_once()
    call = cleanup_setup["ws_svc"].delete_local_branch.await_args
    assert call is not None
    assert call.args[1] == "feature/backend/real-task"


@pytest.mark.asyncio
async def test_cancelled_task_force_deletes_local_branch(
    cleanup_setup: dict[str, Any],
) -> None:
    _task(cleanup_setup, branch="feature/backend/x", status=TaskStatus.CANCELLED)
    await cleanup_setup["db"].flush()

    await cleanup_setup["svc"].cleanup_stale_branches(cleanup_setup["project"].slug)

    cleanup_setup["ws_svc"].delete_local_branch.assert_awaited_once()
    call = cleanup_setup["ws_svc"].delete_local_branch.await_args
    assert call is not None
    assert call.kwargs["force"] is True


@pytest.mark.asyncio
async def test_completed_task_uses_safe_local_delete(
    cleanup_setup: dict[str, Any],
) -> None:
    _task(cleanup_setup, branch="feature/backend/x", status=TaskStatus.COMPLETED)
    await cleanup_setup["db"].flush()

    await cleanup_setup["svc"].cleanup_stale_branches(cleanup_setup["project"].slug)

    cleanup_setup["ws_svc"].delete_local_branch.assert_awaited_once()
    call = cleanup_setup["ws_svc"].delete_local_branch.await_args
    assert call is not None
    assert call.kwargs["force"] is False


@pytest.mark.asyncio
async def test_no_assignee_skips_local_delete_but_still_attempts_remote(
    cleanup_setup: dict[str, Any],
) -> None:
    _task(
        cleanup_setup,
        branch="feature/backend/orphan",
        status=TaskStatus.COMPLETED,
        assigned=False,
    )
    await cleanup_setup["db"].flush()

    result = await cleanup_setup["svc"].cleanup_stale_branches(
        cleanup_setup["project"].slug
    )

    assert result == (1, 0, 1, 0, False)
    cleanup_setup["ws_svc"].delete_local_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_set_truncated_past_the_cap(
    cleanup_setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(GitService, "_CLEANUP_BRANCH_LIMIT", 2)
    for i in range(3):
        _task(
            cleanup_setup,
            branch=f"feature/backend/task-{i}",
            status=TaskStatus.COMPLETED,
        )
    await cleanup_setup["db"].flush()

    result = await cleanup_setup["svc"].cleanup_stale_branches(
        cleanup_setup["project"].slug
    )

    assert result == (2, 2, 0, 0, True)


@pytest.mark.asyncio
async def test_unknown_project_returns_zeroed_result(
    cleanup_setup: dict[str, Any],
) -> None:
    result = await cleanup_setup["svc"].cleanup_stale_branches("does-not-exist")
    assert result == (0, 0, 0, 0, False)


@pytest.mark.asyncio
async def test_delete_task_branch_refuses_env_ladder_rung_directly(
    cleanup_setup: dict[str, Any],
) -> None:
    """The chokepoint guard, not just the sweep's candidate filter: even a
    direct ``delete_task_branch`` call (e.g. the cancel-path caller in
    task.py) must refuse a branch that is an environment-ladder rung — the
    generic ``_delete_remote_branch_best_effort`` primitive's own
    main/master/develop skip predates the ladder model and doesn't know it."""
    project = cleanup_setup["project"]
    project.environments = [
        {"name": "head", "branch": "develop"},
        {"name": "prod", "branch": "master"},
    ]
    await cleanup_setup["db"].flush()

    ok = await cleanup_setup["svc"].delete_task_branch(project.slug, "develop")

    assert ok is False
    remote_delete = cleanup_setup["svc"]._delete_remote_branch_best_effort
    remote_delete.assert_not_awaited()

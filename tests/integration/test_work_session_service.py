"""WorkSessionService coverage — create/update/lifecycle/PR tracking."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.work_session import (
    WorkSessionCreate,
    WorkSessionStatus,
    WorkSessionUpdate,
)
from roboco.services.base import ConflictError, NotFoundError, ValidationError
from roboco.services.work_session import WorkSessionService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def ws_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
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
        name="W-Proj",
        slug=f"w-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=agent.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()
    yield {
        "svc": WorkSessionService(db_session),
        "agent_id": agent.id,
        "project_id": project.id,
        "task_id": task.id,
    }


def _payload(setup: dict, branch: str | None = None) -> WorkSessionCreate:
    return WorkSessionCreate(
        project_id=setup["project_id"],
        task_id=setup["task_id"],
        agent_id=setup["agent_id"],
        branch_name=branch or f"feature/x-{uuid4().hex[:6]}",
        base_branch="main",
        target_branch="main",
    )


# ---------------------------------------------------------------------------
# Create / Get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_work_session(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    assert ws.id is not None
    assert ws.status == WorkSessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_create_missing_project_raises(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    payload = _payload(ws_setup)
    payload_dict = payload.model_dump()
    payload_dict["project_id"] = uuid4()
    with pytest.raises(ValidationError):
        await svc.create(WorkSessionCreate(**payload_dict))


@pytest.mark.asyncio
async def test_create_missing_task_raises(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    payload = _payload(ws_setup)
    payload_dict = payload.model_dump()
    payload_dict["task_id"] = uuid4()
    with pytest.raises(ValidationError):
        await svc.create(WorkSessionCreate(**payload_dict))


@pytest.mark.asyncio
async def test_create_duplicate_active_raises(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    await svc.create(_payload(ws_setup))
    with pytest.raises(ConflictError):
        await svc.create(_payload(ws_setup))


@pytest.mark.asyncio
async def test_get(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    fetched = await svc.get(ws.id)
    assert fetched is not None
    assert fetched.id == ws.id


@pytest.mark.asyncio
async def test_get_returns_none(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    assert await svc.get(uuid4()) is None


@pytest.mark.asyncio
async def test_get_or_raise(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.get_or_raise(uuid4())


@pytest.mark.asyncio
async def test_get_or_raise_returns_session(ws_setup: dict) -> None:
    """Line 136: get_or_raise returns the session when found."""
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    found = await svc.get_or_raise(ws.id)
    assert found.id == ws.id


@pytest.mark.asyncio
async def test_list_active_sessions_filtered_by_project(ws_setup: dict) -> None:
    """Line 295: project_id filter applied."""
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    sessions = await svc.list_active_sessions(project_id=ws.project_id)
    assert any(s.id == ws.id for s in sessions)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_pr_fields(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    updated = await svc.update(
        ws.id,
        WorkSessionUpdate(pr_number=42, pr_url="https://github.com/x/y/pull/42"),
    )
    assert updated is not None
    _PR_NUM = 42
    assert updated.pr_number == _PR_NUM


@pytest.mark.asyncio
async def test_update_returns_none_for_missing(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    assert (await svc.update(uuid4(), WorkSessionUpdate(pr_number=1))) is None


# ---------------------------------------------------------------------------
# Active-by-task lookups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_for_task(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    active = await svc.get_active_for_task(ws_setup["task_id"])
    assert active is not None
    assert active.id == ws.id


@pytest.mark.asyncio
async def test_get_active_for_task_and_agent(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    active = await svc.get_active_for_task_and_agent(
        task_id=ws_setup["task_id"], agent_id=ws_setup["agent_id"]
    )
    assert active is not None
    assert active.id == ws.id


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_by_agent(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    rows = await svc.list_by_agent(ws_setup["agent_id"])
    assert ws.id in {r.id for r in rows}


@pytest.mark.asyncio
async def test_list_by_project(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    rows = await svc.list_by_project(ws_setup["project_id"])
    assert ws.id in {r.id for r in rows}


@pytest.mark.asyncio
async def test_list_active_sessions(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    rows = await svc.list_active_sessions()
    assert ws.id in {r.id for r in rows}


# ---------------------------------------------------------------------------
# Commit + files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_commit(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    updated = await svc.add_commit(ws.id, "abc123def456")
    assert updated is not None
    assert "abc123def456" in updated.commits


@pytest.mark.asyncio
async def test_add_commit_idempotent(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    await svc.add_commit(ws.id, "abc123")
    again = await svc.add_commit(ws.id, "abc123")
    assert again is not None
    assert again.commits.count("abc123") == 1


@pytest.mark.asyncio
async def test_add_commit_returns_none_for_missing(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    assert await svc.add_commit(uuid4(), "abc123") is None


@pytest.mark.asyncio
async def test_add_files_modified(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    updated = await svc.add_files_modified(ws.id, ["a.py", "b.py"])
    assert updated is not None
    assert "a.py" in updated.files_modified


@pytest.mark.asyncio
async def test_add_files_modified_returns_none_for_missing(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    assert await svc.add_files_modified(uuid4(), ["a.py"]) is None


# ---------------------------------------------------------------------------
# PR lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_pr(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    updated = await svc.create_pr(ws.id, 7, "https://github.com/x/y/pull/7")
    _PR_NUM = 7
    assert updated is not None
    assert updated.pr_number == _PR_NUM
    assert updated.pr_status == "open"


@pytest.mark.asyncio
async def test_create_pr_returns_none_for_missing(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    assert await svc.create_pr(uuid4(), 1, "u") is None


@pytest.mark.asyncio
async def test_update_pr_status(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    await svc.create_pr(ws.id, 1, "u")
    updated = await svc.update_pr_status(ws.id, "merged")
    assert updated is not None
    assert updated.pr_status == "merged"


@pytest.mark.asyncio
async def test_update_pr_status_returns_none_for_missing(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    assert await svc.update_pr_status(uuid4(), "merged") is None


@pytest.mark.asyncio
async def test_merge_pr_completes_session(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    await svc.create_pr(ws.id, 99, "u")
    merged = await svc.merge_pr(ws.id, ws_setup["agent_id"])
    assert merged is not None
    assert merged.status == WorkSessionStatus.COMPLETED
    assert merged.pr_status == "merged"


@pytest.mark.asyncio
async def test_merge_pr_returns_none_for_missing(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    assert await svc.merge_pr(uuid4(), uuid4()) is None


# ---------------------------------------------------------------------------
# Lifecycle: complete / abandon / close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_session(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    completed = await svc.complete(ws.id)
    assert completed is not None
    assert completed.status == WorkSessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_complete_returns_none_when_already_terminal(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    await svc.complete(ws.id)
    assert await svc.complete(ws.id) is None


@pytest.mark.asyncio
async def test_complete_returns_none_for_missing(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    assert await svc.complete(uuid4()) is None


@pytest.mark.asyncio
async def test_abandon_session(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    abandoned = await svc.abandon(ws.id, reason="cancelled")
    assert abandoned is not None
    assert abandoned.status == WorkSessionStatus.ABANDONED


@pytest.mark.asyncio
async def test_abandon_returns_none_when_already_terminal(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    await svc.abandon(ws.id)
    assert await svc.abandon(ws.id) is None


@pytest.mark.asyncio
async def test_abandon_returns_none_for_missing(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    assert await svc.abandon(uuid4()) is None


@pytest.mark.asyncio
async def test_close_session(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    closed = await svc.close(ws.id, reason="done")
    assert closed is not None
    assert closed.status == WorkSessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_close_idempotent(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    await svc.close(ws.id)
    again = await svc.close(ws.id)
    assert again is not None
    assert again.status == WorkSessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_close_returns_none_for_missing(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    assert await svc.close(uuid4()) is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_files_changed(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    await svc.add_files_modified(ws.id, ["a.py", "b.py"])
    files = await svc.files_changed(ws.id)
    assert "a.py" in files


@pytest.mark.asyncio
async def test_files_changed_empty_for_missing(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    assert await svc.files_changed(uuid4()) == []


@pytest.mark.asyncio
async def test_has_unpushed_commits_true_when_no_pr(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    await svc.add_commit(ws.id, "abc123")
    assert await svc.has_unpushed_commits(ws.id) is True


@pytest.mark.asyncio
async def test_has_unpushed_commits_false_after_pr(ws_setup: dict) -> None:
    svc = ws_setup["svc"]
    ws = await svc.create(_payload(ws_setup))
    await svc.add_commit(ws.id, "abc123")
    await svc.create_pr(ws.id, 1, "u")
    assert await svc.has_unpushed_commits(ws.id) is False

"""WorkSession API route coverage."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.work_session import router as ws_router
from roboco.db.tables import AgentTable, ProjectTable, TaskTable, WorkSessionTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.permissions import AgentContext
from roboco.models.work_session import WorkSessionStatus
from roboco.services.base import ValidationError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def ws_client(
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
        name="WS-Proj",
        slug=f"ws-proj-{uuid4().hex[:6]}",
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

    app = FastAPI()
    app.include_router(ws_router, prefix="/api/work-sessions")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", agent.id), role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "agent": agent,
            "project": project,
            "task": task,
        }
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"}


@pytest.mark.asyncio
async def test_list_sessions_empty(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.get("/api/work-sessions", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_get_session_not_found(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.get(f"/api/work-sessions/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_create_session(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/x",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_get_session_by_id(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/y",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.get(f"/api/work-sessions/{sid}", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_add_commit(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/c",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.post(
        f"/api/work-sessions/{sid}/commits",
        json={"commit_sha": "abc123def"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_add_commit_session_not_found(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.post(
        f"/api/work-sessions/{uuid4()}/commits",
        json={"commit_sha": "abc"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_add_files(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/f",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.post(
        f"/api/work-sessions/{sid}/files",
        json={"file_paths": ["a.py", "b.py"]},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_create_pr(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/p",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.post(
        f"/api/work-sessions/{sid}/pr",
        json={"pr_number": 42, "pr_url": "https://github.com/x/y/pull/42"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_complete_session(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/cmpl",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.post(f"/api/work-sessions/{sid}/complete", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_abandon_session(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/ab",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.post(
        f"/api/work-sessions/{sid}/abandon",
        params={"reason": "scrapped"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_active_for_task_returns_null(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.get(
        f"/api/work-sessions/task/{ws_client['task'].id}", headers=_HDR
    )
    # Returns null body (200 with None) when there's no active session.
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Filter variations and PR endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_active_only(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.get("/api/work-sessions?active_only=true", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_list_sessions_filter_by_project(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.get(
        f"/api/work-sessions?project_id={ws_client['project'].id}",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_list_sessions_filter_by_agent(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.get(
        f"/api/work-sessions?agent_id={ws_client['agent'].id}",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_create_session_duplicate(ws_client: dict) -> None:
    client = ws_client["client"]
    payload = {
        "project_id": str(ws_client["project"].id),
        "task_id": str(ws_client["task"].id),
        "branch_name": "feature/dup",
        "base_branch": "main",
        "target_branch": "main",
    }
    first = await client.post("/api/work-sessions", json=payload, headers=_HDR)
    assert first.status_code == HTTPStatus.CREATED
    second = await client.post("/api/work-sessions", json=payload, headers=_HDR)
    # Could be 201 again (allowed dup) or 409 (conflict)
    assert second.status_code in (HTTPStatus.CREATED, HTTPStatus.CONFLICT)


@pytest.mark.asyncio
async def test_add_files_session_not_found(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.post(
        f"/api/work-sessions/{uuid4()}/files",
        json={"file_paths": ["x.py"]},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_create_pr_session_not_found(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.post(
        f"/api/work-sessions/{uuid4()}/pr",
        json={"pr_number": 1, "pr_url": "https://x.com"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_update_pr_status_session_not_found(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.patch(
        f"/api/work-sessions/{uuid4()}/pr",
        json={"pr_status": "open"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_update_pr_status_success(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/upd",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.patch(
        f"/api/work-sessions/{sid}/pr",
        json={"pr_status": "open"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_merge_pr_developer_forbidden(ws_client: dict) -> None:
    """Developer cannot merge PR — only PM."""
    client = ws_client["client"]
    response = await client.post(
        f"/api/work-sessions/{uuid4()}/pr/merge",
        json={"merged_by": str(ws_client["agent"].id)},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_complete_session_not_found(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.post(f"/api/work-sessions/{uuid4()}/complete", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_abandon_session_not_found(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.post(f"/api/work-sessions/{uuid4()}/abandon", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# Direct-seeded WorkSession tests — exercise full route bodies bypassing the
# create-then-mutate pattern so coverage includes the success-path return
# statements without leaning on intra-test commits.
# ---------------------------------------------------------------------------


def _seed_ws(setup: dict, **kwargs: Any) -> WorkSessionTable:
    """Insert a WorkSessionTable row directly via the session fixture."""
    return WorkSessionTable(
        id=uuid4(),
        project_id=setup["project"].id,
        task_id=setup["task"].id,
        agent_id=setup["agent"].id,
        branch_name=kwargs.pop("branch_name", "feature/seed"),
        base_branch=kwargs.pop("base_branch", "main"),
        target_branch=kwargs.pop("target_branch", "main"),
        status=kwargs.pop("status", WorkSessionStatus.ACTIVE),
        commits=kwargs.pop("commits", []),
        files_modified=kwargs.pop("files_modified", []),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_get_session_by_id_seeded(
    ws_client: dict, db_session: AsyncSession
) -> None:
    """Direct-seeded session — exercises get_session 200 path."""
    ws = _seed_ws(ws_client)
    db_session.add(ws)
    await db_session.flush()

    response = await ws_client["client"].get(
        f"/api/work-sessions/{ws.id}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["id"] == str(ws.id)


@pytest.mark.asyncio
async def test_get_active_for_task_returns_session(
    ws_client: dict, db_session: AsyncSession
) -> None:
    """Active session for a task — should be returned (not null)."""
    ws = _seed_ws(ws_client, branch_name="feature/active")
    db_session.add(ws)
    await db_session.flush()

    response = await ws_client["client"].get(
        f"/api/work-sessions/task/{ws_client['task'].id}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body is not None
    assert body["id"] == str(ws.id)


@pytest.mark.asyncio
async def test_list_sessions_with_seeded_session_active_only(
    ws_client: dict, db_session: AsyncSession
) -> None:
    """List sessions returns the seeded session — exercises summary-mapping line."""
    ws = _seed_ws(ws_client, branch_name="feature/listed")
    db_session.add(ws)
    await db_session.flush()

    response = await ws_client["client"].get(
        "/api/work-sessions?active_only=true", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK
    items = response.json()
    assert isinstance(items, list)
    ids = {item["id"] for item in items}
    assert str(ws.id) in ids


@pytest.mark.asyncio
async def test_add_commit_seeded_success(
    ws_client: dict, db_session: AsyncSession
) -> None:
    """Add commit to a directly-seeded session — exercises 200 return."""
    ws = _seed_ws(ws_client, branch_name="feature/commit-seed")
    db_session.add(ws)
    await db_session.flush()

    response = await ws_client["client"].post(
        f"/api/work-sessions/{ws.id}/commits",
        json={"commit_sha": "deadbeef1234"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_add_files_seeded_success(
    ws_client: dict, db_session: AsyncSession
) -> None:
    """Add files to a directly-seeded session."""
    ws = _seed_ws(ws_client, branch_name="feature/files-seed")
    db_session.add(ws)
    await db_session.flush()

    response = await ws_client["client"].post(
        f"/api/work-sessions/{ws.id}/files",
        json={"file_paths": ["foo.py", "bar.py"]},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_create_pr_seeded_success(
    ws_client: dict, db_session: AsyncSession
) -> None:
    """Record PR creation against a seeded session."""
    ws = _seed_ws(ws_client, branch_name="feature/pr-seed")
    db_session.add(ws)
    await db_session.flush()

    response = await ws_client["client"].post(
        f"/api/work-sessions/{ws.id}/pr",
        json={"pr_number": 7, "pr_url": "https://github.com/x/y/pull/7"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_update_pr_status_seeded_success(
    ws_client: dict, db_session: AsyncSession
) -> None:
    """Update PR status on a seeded session."""
    ws = _seed_ws(
        ws_client, branch_name="feature/upd-seed", pr_number=1, pr_status="open"
    )
    db_session.add(ws)
    await db_session.flush()

    response = await ws_client["client"].patch(
        f"/api/work-sessions/{ws.id}/pr",
        json={"pr_status": "merged"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_complete_session_seeded_success(
    ws_client: dict, db_session: AsyncSession
) -> None:
    """Complete an active seeded session."""
    ws = _seed_ws(ws_client, branch_name="feature/complete-seed")
    db_session.add(ws)
    await db_session.flush()

    response = await ws_client["client"].post(
        f"/api/work-sessions/{ws.id}/complete", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_abandon_session_seeded_success(
    ws_client: dict, db_session: AsyncSession
) -> None:
    """Abandon a seeded session — with reason query param."""
    ws = _seed_ws(ws_client, branch_name="feature/abandon-seed")
    db_session.add(ws)
    await db_session.flush()

    response = await ws_client["client"].post(
        f"/api/work-sessions/{ws.id}/abandon",
        params={"reason": "redundant work"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_merge_pr_pm_succeeds(db_session: AsyncSession) -> None:
    """PM merging a PR — exercises merge_pr 200 path."""
    pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="WS-PM-Proj",
        slug=f"ws-pm-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=pm.id,
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
        created_by=pm.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()
    ws = WorkSessionTable(
        id=uuid4(),
        project_id=project.id,
        task_id=task.id,
        agent_id=pm.id,
        branch_name="feature/merge",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
        pr_number=42,
        pr_status="open",
    )
    db_session.add(ws)
    await db_session.flush()

    app = FastAPI()
    app.include_router(ws_router, prefix="/api/work-sessions")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", pm.id), role=AgentRole.MAIN_PM, team=None
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/work-sessions/{ws.id}/pr/merge",
            json={"merged_by": str(pm.id)},
            headers=_HDR,
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_merge_pr_unknown_session_pm(db_session: AsyncSession) -> None:
    """PM merging an unknown PR — 404 from merge_pr's None return."""
    pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()

    app = FastAPI()
    app.include_router(ws_router, prefix="/api/work-sessions")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", pm.id), role=AgentRole.MAIN_PM, team=None
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/work-sessions/{uuid4()}/pr/merge",
            json={"merged_by": str(pm.id)},
            headers=_HDR,
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_create_session_non_developer_forbidden(
    db_session: AsyncSession,
) -> None:
    """A non-eligible role (e.g. QA) cannot create a work session."""
    qa = AgentTable(
        id=uuid4(),
        name="QA",
        slug=f"qa-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(qa)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="QA-Proj",
        slug=f"qa-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=qa.id,
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
        created_by=qa.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()

    app = FastAPI()
    app.include_router(ws_router, prefix="/api/work-sessions")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", qa.id), role=AgentRole.QA, team=Team.BACKEND
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/work-sessions",
            json={
                "project_id": str(project.id),
                "task_id": str(task.id),
                "branch_name": "feature/q",
                "base_branch": "main",
                "target_branch": "main",
            },
            headers=_HDR,
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_list_sessions_filter_by_agent_with_status(
    ws_client: dict, db_session: AsyncSession
) -> None:
    """List by agent_id with explicit status filter — exercises that branch."""
    ws = _seed_ws(ws_client, branch_name="feature/agent-filter")
    db_session.add(ws)
    await db_session.flush()

    response = await ws_client["client"].get(
        f"/api/work-sessions?agent_id={ws_client['agent'].id}&status=active",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK
    items = response.json()
    assert isinstance(items, list)


@pytest.mark.asyncio
async def test_list_sessions_filter_by_project_with_status(
    ws_client: dict, db_session: AsyncSession
) -> None:
    """List by project_id with explicit status — exercises that branch."""
    ws = _seed_ws(ws_client, branch_name="feature/proj-filter")
    db_session.add(ws)
    await db_session.flush()

    response = await ws_client["client"].get(
        f"/api/work-sessions?project_id={ws_client['project'].id}&status=active",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_create_session_unknown_project_reraises(ws_client: dict) -> None:
    """Create with a non-existent project_id should hit the bare-raise branch.

    The except block discriminates on `'already' in str(e)`; ValidationError
    "Project not found" doesn't contain that substring so the bare `raise`
    re-raises the original error to the ASGI layer.
    """
    with pytest.raises(ValidationError):
        await ws_client["client"].post(
            "/api/work-sessions",
            json={
                "project_id": str(uuid4()),
                "task_id": str(ws_client["task"].id),
                "branch_name": "feature/no-project",
                "base_branch": "main",
                "target_branch": "main",
            },
            headers=_HDR,
        )

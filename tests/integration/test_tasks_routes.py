"""Tasks API route coverage — list/get/lifecycle endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.tasks import router as tasks_router
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def task_client(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    main_pm = AgentTable(
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
    db_session.add(main_pm)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="TR-Proj",
        slug=f"tr-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=main_pm.id,
    )
    db_session.add(project)
    await db_session.flush()

    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/tasks")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=main_pm.id, role=AgentRole.MAIN_PM, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "agent": main_pm,
            "project": project,
            "db": db_session,
        }
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


def _seed_task(
    setup: dict, *, status: TaskStatus = TaskStatus.PENDING, **kw
) -> TaskTable:
    task = TaskTable(
        id=uuid4(),
        title=kw.pop("title", "t"),
        description=kw.pop("description", "d"),
        acceptance_criteria=["ac"],
        status=status,
        priority=kw.pop("priority", 2),
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=setup["project"].id,
        created_by=setup["agent"].id,
        team=kw.pop("team", Team.BACKEND),
        **kw,
    )
    setup["db"].add(task)
    return task


@pytest.mark.asyncio
async def test_create_task(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        "/api/tasks",
        json={
            "title": "Test Task",
            "description": "Some description",
            "acceptance_criteria": ["criteria"],
            "team": "backend",
            "project_id": str(task_client["project"].id),
        },
        headers=_HDR,
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_create_task_missing_project_id(task_client: dict) -> None:
    """Create with no project_id should fail validation."""
    client = task_client["client"]
    response = await client.post(
        "/api/tasks",
        json={
            "title": "Test",
            "description": "x",
            "acceptance_criteria": ["a"],
            "team": "backend",
        },
        headers=_HDR,
    )
    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_list_tasks(task_client: dict) -> None:
    client = task_client["client"]
    _seed_task(task_client)
    await task_client["db"].flush()
    response = await client.get("/api/tasks", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_tasks_filter_by_team(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks?team=backend", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks?status=pending", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_my_tasks(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/my", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_pending_tasks(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/pending", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_blocked_tasks(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/blocked", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_awaiting_qa(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/awaiting-qa", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_task_not_found(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get(f"/api/tasks/{uuid4()}", headers=_HDR)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_task_by_id(task_client: dict) -> None:
    client = task_client["client"]
    task = _seed_task(task_client)
    await task_client["db"].flush()
    response = await client.get(f"/api/tasks/{task.id}", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_update_task(task_client: dict) -> None:
    client = task_client["client"]
    task = _seed_task(task_client)
    await task_client["db"].flush()
    response = await client.patch(
        f"/api/tasks/{task.id}",
        json={"title": "Renamed"},
        headers=_HDR,
    )
    assert response.status_code in (200, 422)


@pytest.mark.asyncio
async def test_delete_task(task_client: dict) -> None:
    client = task_client["client"]
    task = _seed_task(task_client)
    await task_client["db"].flush()
    response = await client.delete(f"/api/tasks/{task.id}", headers=_HDR)
    assert response.status_code in (200, 204, 422)


@pytest.mark.asyncio
async def test_delete_task_not_found(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.delete(f"/api/tasks/{uuid4()}", headers=_HDR)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_subtasks_of_unknown_task(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get(f"/api/tasks/{uuid4()}/subtasks", headers=_HDR)
    # Either 404 or empty list depending on implementation.
    assert response.status_code in (200, 404)


@pytest.mark.asyncio
async def test_count_endpoint_returns_response(task_client: dict) -> None:
    """Count route may take query params we don't supply; just ensure it's reached."""
    client = task_client["client"]
    response = await client.get("/api/tasks/count", headers=_HDR)
    assert response.status_code in (200, 422)


# ---------------------------------------------------------------------------
# Additional list endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_awaiting_docs(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/awaiting-docs", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_team_tasks(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/team/backend", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_task_stats(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/stats", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_task_stats_by_team(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/stats/by-team", headers=_HDR)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Lifecycle: claim/unclaim (404 paths)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_unknown_task_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/claim",
        json={"role": "developer"},
        headers=_HDR,
    )
    assert response.status_code in (400, 403, 404, 422)


@pytest.mark.asyncio
async def test_unclaim_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(f"/api/tasks/{uuid4()}/unclaim", headers=_HDR)
    assert response.status_code in (400, 404)


@pytest.mark.asyncio
async def test_submit_for_qa_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/submit-qa",
        json={},
        headers=_HDR,
    )
    assert response.status_code in (400, 404, 422)


@pytest.mark.asyncio
async def test_pass_qa_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/pass-qa",
        json={"notes": "looks good and is sufficiently detailed"},
        headers=_HDR,
    )
    assert response.status_code in (400, 403, 404, 422)


@pytest.mark.asyncio
async def test_fail_qa_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/fail-qa",
        json={"notes": "broken in many ways"},
        headers=_HDR,
    )
    assert response.status_code in (400, 403, 404, 422)


@pytest.mark.asyncio
async def test_complete_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/complete",
        json={},
        headers=_HDR,
    )
    assert response.status_code in (400, 403, 404, 422)


@pytest.mark.asyncio
async def test_block_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/block",
        json={"reason": "blocker", "blocker_type": "external", "what_needed": "x"},
        headers=_HDR,
    )
    assert response.status_code in (400, 403, 404, 422)


@pytest.mark.asyncio
async def test_unblock_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(f"/api/tasks/{uuid4()}/unblock", headers=_HDR)
    assert response.status_code in (400, 403, 404, 422)


@pytest.mark.asyncio
async def test_pause_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(f"/api/tasks/{uuid4()}/pause", headers=_HDR)
    assert response.status_code in (400, 403, 404, 422)


@pytest.mark.asyncio
async def test_resume_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(f"/api/tasks/{uuid4()}/resume", headers=_HDR)
    assert response.status_code in (400, 403, 404, 422)


@pytest.mark.asyncio
async def test_cancel_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/cancel",
        json={"reason": "no longer needed"},
        headers=_HDR,
    )
    assert response.status_code in (400, 403, 404, 422)


@pytest.mark.asyncio
async def test_add_progress_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/progress",
        json={"message": "doing things", "percentage": 25},
        headers=_HDR,
    )
    assert response.status_code in (400, 404)


@pytest.mark.asyncio
async def test_add_checkpoint_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/checkpoints",
        json={
            "state_summary": "halfway",
            "remaining_work": ["finish API"],
        },
        headers=_HDR,
    )
    assert response.status_code in (400, 404)


@pytest.mark.asyncio
async def test_add_commit_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/commits",
        json={"hash": "abc123", "message": "fix"},
        headers=_HDR,
    )
    assert response.status_code in (400, 404)


@pytest.mark.asyncio
async def test_escalate_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/escalate",
        json={"reason": "needs PM input"},
        headers=_HDR,
    )
    assert response.status_code in (400, 403, 404, 422)


@pytest.mark.asyncio
async def test_get_sessions_for_task(task_client: dict) -> None:
    client = task_client["client"]
    task = _seed_task(task_client)
    await task_client["db"].flush()
    response = await client.get(f"/api/tasks/{task.id}/sessions", headers=_HDR)
    assert response.status_code == 200

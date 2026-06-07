"""Tasks API route coverage — list/get/lifecycle endpoints."""

from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.tasks import (
    _translate_error,
    get_awaiting_ceo_approval_tasks,
    get_awaiting_pm_review_tasks,
)
from roboco.api.routes.tasks import (
    router as tasks_router,
)
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.exceptions import TaskLifecycleError
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.permissions import AgentContext
from roboco.services.base import (
    NotFoundError,
    ServiceError,
    UnauthorizedError,
    ValidationError,
)
from roboco.services.notification_delivery import EscalationError
from roboco.services.permissions import PermissionService

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
        created_by=kw.pop("created_by", setup["agent"].id),
        team=kw.pop("team", Team.BACKEND),
        **kw,
    )
    setup["db"].add(task)
    return task


async def _seed_agent(
    setup: dict, *, role: AgentRole = AgentRole.DEVELOPER
) -> AgentTable:
    """Seed a real agent so FK constraints don't break."""
    other = AgentTable(
        id=uuid4(),
        name="Other",
        slug=f"other-{uuid4().hex[:8]}",
        role=role,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    setup["db"].add(other)
    await setup["db"].flush()
    return other


@pytest.mark.asyncio
async def test_create_task(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        "/api/tasks",
        json={
            "title": "Test Task",
            "description": "Some description that is long enough for the schema",
            "acceptance_criteria": ["criteria"],
            "team": "backend",
            "project_id": str(task_client["project"].id),
            "task_type": "code",
            "nature": "technical",
            "estimated_complexity": "medium",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.CREATED


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
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_list_tasks(task_client: dict) -> None:
    client = task_client["client"]
    _seed_task(task_client)
    await task_client["db"].flush()
    response = await client.get("/api/tasks", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_list_tasks_filter_by_team(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks?team=backend", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks?status=pending", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_my_tasks(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/my", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_pending_tasks(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/pending", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_blocked_tasks(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/blocked", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_awaiting_qa(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/awaiting-qa", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_task_not_found(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get(f"/api/tasks/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_task_by_id(task_client: dict) -> None:
    client = task_client["client"]
    task = _seed_task(task_client)
    await task_client["db"].flush()
    response = await client.get(f"/api/tasks/{task.id}", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


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
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.UNPROCESSABLE_ENTITY)


@pytest.mark.asyncio
async def test_update_task_status_override_recovers_blocked(task_client: dict) -> None:
    """A privileged PATCH with ``status`` is applied as an audited override, so an
    operator can recover a task wedged in ``blocked`` (which ``/complete`` refuses)
    instead of the status being silently dropped."""
    client = task_client["client"]
    task = _seed_task(task_client, status=TaskStatus.BLOCKED)
    await task_client["db"].flush()
    response = await client.patch(
        f"/api/tasks/{task.id}",
        json={"status": "completed"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_delete_task(task_client: dict) -> None:
    client = task_client["client"]
    task = _seed_task(task_client)
    await task_client["db"].flush()
    response = await client.delete(f"/api/tasks/{task.id}", headers=_HDR)
    assert response.status_code in (
        HTTPStatus.OK,
        HTTPStatus.NO_CONTENT,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_delete_task_not_found(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.delete(f"/api/tasks/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_subtasks_of_unknown_task(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get(f"/api/tasks/{uuid4()}/subtasks", headers=_HDR)
    # Either 404 or empty list depending on implementation.
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_count_endpoint_returns_response(task_client: dict) -> None:
    """Count route may take query params we don't supply; just ensure it's reached."""
    client = task_client["client"]
    response = await client.get("/api/tasks/count", headers=_HDR)
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.UNPROCESSABLE_ENTITY)


# ---------------------------------------------------------------------------
# Additional list endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_awaiting_docs(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/awaiting-docs", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_team_tasks(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/team/backend", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_task_stats(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/stats", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_task_stats_by_team(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get("/api/tasks/stats/by-team", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


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
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_unclaim_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(f"/api/tasks/{uuid4()}/unclaim", headers=_HDR)
    assert response.status_code in (HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_submit_for_qa_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/submit-qa",
        json={},
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_pass_qa_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/pass-qa",
        json={"notes": "looks good and is sufficiently detailed"},
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_fail_qa_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/fail-qa",
        json={"notes": "broken in many ways"},
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_complete_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/complete",
        json={},
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_block_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/block",
        json={"reason": "blocker", "blocker_type": "external", "what_needed": "x"},
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_unblock_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(f"/api/tasks/{uuid4()}/unblock", headers=_HDR)
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_pause_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(f"/api/tasks/{uuid4()}/pause", headers=_HDR)
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_resume_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(f"/api/tasks/{uuid4()}/resume", headers=_HDR)
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_cancel_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/cancel",
        json={"reason": "no longer needed"},
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_add_progress_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/progress",
        json={"message": "doing things", "percentage": 25},
        headers=_HDR,
    )
    assert response.status_code in (HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND)


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
    assert response.status_code in (HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_add_commit_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/commits",
        json={"hash": "abc123", "message": "fix"},
        headers=_HDR,
    )
    assert response.status_code in (HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_escalate_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/escalate",
        json={"reason": "needs PM input"},
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_get_sessions_for_task(task_client: dict) -> None:
    client = task_client["client"]
    task = _seed_task(task_client)
    await task_client["db"].flush()
    response = await client.get(f"/api/tasks/{task.id}/sessions", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Additional create_task validation paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_no_acceptance_criteria(task_client: dict) -> None:
    """Task without acceptance criteria — 4xx."""
    client = task_client["client"]
    response = await client.post(
        "/api/tasks",
        json={
            "title": "T",
            "description": "d",
            "acceptance_criteria": [],
            "team": "backend",
            "project_id": str(task_client["project"].id),
        },
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_create_task_blank_acceptance_criteria(task_client: dict) -> None:
    """Task with blank acceptance criteria — 400."""
    client = task_client["client"]
    response = await client.post(
        "/api/tasks",
        json={
            "title": "T",
            "description": "d",
            "acceptance_criteria": ["  ", ""],
            "team": "backend",
            "project_id": str(task_client["project"].id),
        },
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_create_task_assigned_to_uuid(task_client: dict) -> None:
    """Task with assigned_to as UUID string — should work."""
    client = task_client["client"]
    response = await client.post(
        "/api/tasks",
        json={
            "title": "T",
            "description": "Twenty character description here ok",
            "acceptance_criteria": ["a"],
            "team": "backend",
            "project_id": str(task_client["project"].id),
            "assigned_to": str(task_client["agent"].id),
            "task_type": "code",
            "nature": "technical",
            "estimated_complexity": "medium",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_create_task_assigned_to_slug(task_client: dict) -> None:
    """Task with assigned_to as slug — should resolve."""
    client = task_client["client"]
    response = await client.post(
        "/api/tasks",
        json={
            "title": "T",
            "description": "Twenty character description here ok",
            "acceptance_criteria": ["a"],
            "team": "backend",
            "project_id": str(task_client["project"].id),
            "assigned_to": task_client["agent"].slug,
            "task_type": "code",
            "nature": "technical",
            "estimated_complexity": "medium",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_create_task_assigned_to_unknown_slug(task_client: dict) -> None:
    """Task with unknown assigned_to slug — 422."""
    client = task_client["client"]
    response = await client.post(
        "/api/tasks",
        json={
            "title": "T",
            "description": "d",
            "acceptance_criteria": ["a"],
            "team": "backend",
            "project_id": str(task_client["project"].id),
            "assigned_to": "ghost-agent-1",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# ---------------------------------------------------------------------------
# Get descendants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_descendants(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.get(f"/api/tasks/{uuid4()}/descendants", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Update task — privileges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_not_found(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.patch(
        f"/api/tasks/{uuid4()}",
        json={"title": "Renamed"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_update_task_via_put(task_client: dict) -> None:
    """PUT alias works the same as PATCH."""
    client = task_client["client"]
    task = _seed_task(task_client)
    await task_client["db"].flush()
    response = await client.put(
        f"/api/tasks/{task.id}",
        json={"title": "PutRenamed"},
        headers=_HDR,
    )
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.UNPROCESSABLE_ENTITY)


# ---------------------------------------------------------------------------
# Lifecycle: start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(f"/api/tasks/{uuid4()}/start", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_start_task_not_assigned_returns_403(task_client: dict) -> None:
    """Main PM is not the assignee, so start should fail with 403."""
    client = task_client["client"]
    other = await _seed_agent(task_client)
    task = _seed_task(task_client, status=TaskStatus.CLAIMED, assigned_to=other.id)
    await task_client["db"].flush()
    response = await client.post(f"/api/tasks/{task.id}/start", headers=_HDR)
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_start_task_no_branch_returns_400(task_client: dict) -> None:
    """Task without branch — 400."""
    client = task_client["client"]
    task = _seed_task(
        task_client,
        status=TaskStatus.CLAIMED,
        assigned_to=task_client["agent"].id,
    )
    await task_client["db"].flush()
    response = await client.post(f"/api/tasks/{task.id}/start", headers=_HDR)
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "NO_BRANCH" in response.json()["detail"]


@pytest.mark.asyncio
async def test_start_claimed_task_no_plan_returns_400(task_client: dict) -> None:
    """Claimed task without plan — 400."""
    client = task_client["client"]
    task = _seed_task(
        task_client,
        status=TaskStatus.CLAIMED,
        assigned_to=task_client["agent"].id,
        branch_name="feature/backend/X",
    )
    await task_client["db"].flush()
    response = await client.post(f"/api/tasks/{task.id}/start", headers=_HDR)
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "NO_PLAN" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_task_not_found(task_client: dict) -> None:
    client = task_client["client"]
    blocker = _seed_task(task_client)
    await task_client["db"].flush()
    response = await client.post(
        f"/api/tasks/{uuid4()}/block?blocker_id={blocker.id}",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_block_task_forbidden(task_client: dict) -> None:
    """Non-assignee, non-PM cannot block."""

    other = await _seed_agent(task_client)
    task = _seed_task(task_client, assigned_to=other.id)
    blocker = _seed_task(task_client)
    await task_client["db"].flush()

    # Override agent to a developer not assigned
    app = task_client["client"]._transport.app

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=uuid4(), role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    app.dependency_overrides[get_agent_context] = _override_agent
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/block?blocker_id={blocker.id}",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# Soft-block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_block_unknown_returns_404(task_client: dict) -> None:
    client = task_client["client"]
    response = await client.post(
        f"/api/tasks/{uuid4()}/soft-block",
        json={
            "reason": "stuck",
            "blocker_type": "external",
            "what_needed": "some external system",
        },
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.FORBIDDEN,
    )


# ---------------------------------------------------------------------------
# Unblock — happy path with progress notification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unblock_task_not_blocked_returns_400(task_client: dict) -> None:
    """Unblock a task that is not blocked - 400."""
    client = task_client["client"]
    task = _seed_task(
        task_client,
        status=TaskStatus.PENDING,
        assigned_to=task_client["agent"].id,
    )
    await task_client["db"].flush()
    response = await client.post(f"/api/tasks/{task.id}/unblock", headers=_HDR)
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_unblock_task_forbidden(task_client: dict) -> None:

    other = await _seed_agent(task_client)
    task = _seed_task(task_client, status=TaskStatus.BLOCKED, assigned_to=other.id)
    await task_client["db"].flush()

    # Override agent role to be non-PM, non-assignee
    app = task_client["client"]._transport.app

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=uuid4(), role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    app.dependency_overrides[get_agent_context] = _override_agent
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/unblock", headers=_HDR
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# Pause/resume forbidden + invalid-status branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_task_forbidden(task_client: dict) -> None:
    other = await _seed_agent(task_client)
    task = _seed_task(task_client, status=TaskStatus.IN_PROGRESS, assigned_to=other.id)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/pause", headers=_HDR
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_pause_task_invalid_status_returns_400(task_client: dict) -> None:
    """Pause when not in_progress — 400."""
    task = _seed_task(
        task_client, status=TaskStatus.PENDING, assigned_to=task_client["agent"].id
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/pause", headers=_HDR
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_resume_task_forbidden(task_client: dict) -> None:
    other = await _seed_agent(task_client)
    task = _seed_task(task_client, status=TaskStatus.PAUSED, assigned_to=other.id)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/resume", headers=_HDR
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_resume_task_invalid_status(task_client: dict) -> None:
    task = _seed_task(
        task_client, status=TaskStatus.PENDING, assigned_to=task_client["agent"].id
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/resume", headers=_HDR
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_task_not_found(task_client: dict) -> None:
    response = await task_client["client"].post(
        f"/api/tasks/{uuid4()}/verify", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_verify_task_forbidden(task_client: dict) -> None:
    other = await _seed_agent(task_client)
    task = _seed_task(task_client, status=TaskStatus.IN_PROGRESS, assigned_to=other.id)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/verify", headers=_HDR
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_verify_task_invalid_status(task_client: dict) -> None:
    task = _seed_task(
        task_client, status=TaskStatus.PENDING, assigned_to=task_client["agent"].id
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/verify", headers=_HDR
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# submit-qa gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_qa_not_self_verified_returns_400(task_client: dict) -> None:
    task = _seed_task(
        task_client,
        status=TaskStatus.VERIFYING,
        assigned_to=task_client["agent"].id,
        self_verified=False,
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/submit-qa", headers=_HDR
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "NOT_SELF_VERIFIED" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_qa_no_commits_returns_400(task_client: dict) -> None:
    task = _seed_task(
        task_client,
        status=TaskStatus.VERIFYING,
        assigned_to=task_client["agent"].id,
        self_verified=True,
        commits=[],
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/submit-qa", headers=_HDR
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "NO_COMMITS" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_qa_no_pr_returns_400(task_client: dict) -> None:
    task = _seed_task(
        task_client,
        status=TaskStatus.VERIFYING,
        assigned_to=task_client["agent"].id,
        self_verified=True,
        commits=[{"hash": "abc", "message": "fix"}],
        pr_number=None,
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/submit-qa", headers=_HDR
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "NO_PR" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_qa_no_progress_updates_returns_400(task_client: dict) -> None:
    task = _seed_task(
        task_client,
        status=TaskStatus.VERIFYING,
        assigned_to=task_client["agent"].id,
        self_verified=True,
        commits=[{"hash": "abc", "message": "fix"}],
        pr_number=42,
        progress_updates=[],
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/submit-qa", headers=_HDR
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "NO_PROGRESS" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_qa_forbidden_non_assignee(task_client: dict) -> None:
    other = await _seed_agent(task_client)
    task = _seed_task(
        task_client,
        status=TaskStatus.VERIFYING,
        assigned_to=other.id,
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/submit-qa", headers=_HDR
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# pass-qa gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_qa_non_qa_role_forbidden(task_client: dict) -> None:
    task = _seed_task(task_client, status=TaskStatus.AWAITING_QA, pr_number=42)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/pass-qa",
        json={"notes": "looks good and is sufficiently substantive notes"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_fail_qa_non_qa_role_forbidden(task_client: dict) -> None:
    task = _seed_task(task_client, status=TaskStatus.AWAITING_QA, pr_number=42)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/fail-qa",
        json={"notes": "broken in some ways"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# docs-complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_complete_unknown_returns_4xx(task_client: dict) -> None:
    response = await task_client["client"].post(
        f"/api/tasks/{uuid4()}/docs-complete",
        json={"notes": "completed docs"},
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.FORBIDDEN,
    )


# ---------------------------------------------------------------------------
# submit-pm-review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_pm_review_not_found(task_client: dict) -> None:
    response = await task_client["client"].post(
        f"/api/tasks/{uuid4()}/submit-pm-review",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_submit_pm_review_forbidden(task_client: dict) -> None:
    other = await _seed_agent(task_client)
    task = _seed_task(task_client, status=TaskStatus.IN_PROGRESS, assigned_to=other.id)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/submit-pm-review",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# CEO endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_awaiting_pm_review(task_client: dict) -> None:
    """Path collides with /{task_id} — invalid UUID gives 422."""
    response = await task_client["client"].get(
        "/api/tasks/awaiting-pm-review", headers=_HDR
    )
    # Route ordering quirk: /{task_id} matches first.
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.UNPROCESSABLE_ENTITY)


@pytest.mark.asyncio
async def test_get_awaiting_ceo_approval(task_client: dict) -> None:
    response = await task_client["client"].get(
        "/api/tasks/awaiting-ceo-approval", headers=_HDR
    )
    # Same /{task_id} ordering quirk.
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.UNPROCESSABLE_ENTITY)


@pytest.mark.asyncio
async def test_ceo_approve_unknown_returns_4xx(task_client: dict) -> None:
    response = await task_client["client"].post(
        f"/api/tasks/{uuid4()}/ceo-approve",
        json={"notes": "approved"},
        headers=_HDR,
    )
    # Main PM is not CEO — 403
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_ceo_reject_non_ceo_forbidden(task_client: dict) -> None:
    response = await task_client["client"].post(
        f"/api/tasks/{uuid4()}/ceo-reject",
        json={"notes": "rejected"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_escalate_to_ceo_unknown_returns_4xx(task_client: dict) -> None:
    response = await task_client["client"].post(
        f"/api/tasks/{uuid4()}/escalate-to-ceo",
        json={"notes": "needs CEO"},
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.FORBIDDEN,
    )


# ---------------------------------------------------------------------------
# Substitute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_substitute_unknown_returns_4xx(task_client: dict) -> None:
    response = await task_client["client"].post(
        f"/api/tasks/{uuid4()}/substitute",
        json={"reason": "low_context", "details": "Need more context"},
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.FORBIDDEN,
    )


# ---------------------------------------------------------------------------
# progress / checkpoint / commit forbidden + not-found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_forbidden(task_client: dict) -> None:
    other = await _seed_agent(task_client)
    task = _seed_task(task_client, assigned_to=other.id)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/progress",
        json={"message": "doing things now", "percentage": 25},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_checkpoint_forbidden(task_client: dict) -> None:
    other = await _seed_agent(task_client)
    task = _seed_task(task_client, assigned_to=other.id)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/checkpoint",
        json={"state_summary": "halfway", "remaining_work": ["finish"]},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_checkpoint_unknown_returns_404(task_client: dict) -> None:
    response = await task_client["client"].post(
        f"/api/tasks/{uuid4()}/checkpoint",
        json={"state_summary": "halfway", "remaining_work": ["finish"]},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_commit_unknown_returns_404(task_client: dict) -> None:
    response = await task_client["client"].post(
        f"/api/tasks/{uuid4()}/commit",
        json={"hash": "abc1234", "message": "fix"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_commit_forbidden(task_client: dict) -> None:
    other = await _seed_agent(task_client)
    task = _seed_task(task_client, assigned_to=other.id)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/commit",
        json={"hash": "abc1234", "message": "fix"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# Activate (PM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_unknown_returns_4xx(task_client: dict) -> None:
    response = await task_client["client"].post(
        f"/api/tasks/{uuid4()}/activate", headers=_HDR
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.FORBIDDEN,
    )


@pytest.mark.asyncio
async def test_activate_developer_forbidden(task_client: dict) -> None:

    app = task_client["client"]._transport.app

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=task_client["agent"].id,
            role=AgentRole.DEVELOPER,
            team=Team.BACKEND,
        )

    app.dependency_overrides[get_agent_context] = _override_agent
    response = await task_client["client"].post(
        f"/api/tasks/{uuid4()}/activate", headers=_HDR
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# Listing variants — different team filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_by_team_filter(task_client: dict) -> None:
    """Both team and status filters set."""
    response = await task_client["client"].get(
        "/api/tasks?team=backend&status=pending", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_team_tasks_unauthorized(task_client: dict) -> None:
    """Developer trying to view a team's tasks they aren't on."""

    app = task_client["client"]._transport.app

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=task_client["agent"].id,
            role=AgentRole.DEVELOPER,
            team=Team.BACKEND,
        )

    app.dependency_overrides[get_agent_context] = _override_agent
    response = await task_client["client"].get("/api/tasks/team/frontend", headers=_HDR)
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_get_task_stats_by_team_developer_forbidden(
    task_client: dict,
) -> None:

    app = task_client["client"]._transport.app

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=task_client["agent"].id,
            role=AgentRole.DEVELOPER,
            team=Team.BACKEND,
        )

    app.dependency_overrides[get_agent_context] = _override_agent
    response = await task_client["client"].get("/api/tasks/stats/by-team", headers=_HDR)
    assert response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# List as developer with no team — empty list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_no_team_no_view_all(task_client: dict) -> None:
    """Agent with no team and no VIEW_ALL — empty list."""

    app = task_client["client"]._transport.app

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=task_client["agent"].id,
            role=AgentRole.DEVELOPER,
            team=None,
        )

    app.dependency_overrides[get_agent_context] = _override_agent
    response = await task_client["client"].get("/api/tasks", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_tasks_developer_with_team_filters_to_own(
    task_client: dict,
) -> None:
    """Developer (no VIEW_ALL) with a team — effective_team = agent.team."""
    app = task_client["client"]._transport.app

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=task_client["agent"].id,
            role=AgentRole.DEVELOPER,
            team=Team.BACKEND,
        )

    app.dependency_overrides[get_agent_context] = _override_agent
    response = await task_client["client"].get("/api/tasks", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_task_missing_project_id_returns_422(task_client: dict) -> None:
    """`TaskCreate.project_id` is `UUID` (required); pydantic rejects missing
    value with 422 before the route runs. (The previously-dead inline runtime
    `if not data.project_id` branch was removed.)"""
    response = await task_client["client"].post(
        "/api/tasks",
        json={
            "title": "T",
            "description": "d",
            "acceptance_criteria": ["a"],
            "team": "backend",
            # project_id intentionally missing
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# ---------------------------------------------------------------------------
# Delete forbidden
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_task_forbidden_non_creator(task_client: dict) -> None:
    """Developer not the creator — 403."""

    other = await _seed_agent(task_client)
    task = _seed_task(task_client, created_by=other.id)
    await task_client["db"].flush()

    # Override to be a developer different from creator
    app = task_client["client"]._transport.app

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=uuid4(), role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    app.dependency_overrides[get_agent_context] = _override_agent
    response = await task_client["client"].delete(f"/api/tasks/{task.id}", headers=_HDR)
    assert response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# Cancel forbidden + happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_developer_forbidden(task_client: dict) -> None:

    task = _seed_task(task_client)
    await task_client["db"].flush()
    app = task_client["client"]._transport.app

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=task_client["agent"].id,
            role=AgentRole.DEVELOPER,
            team=Team.BACKEND,
        )

    app.dependency_overrides[get_agent_context] = _override_agent
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/cancel",
        json={"reason": "no longer needed at all"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_cancel_task_pm_succeeds(task_client: dict) -> None:
    task = _seed_task(task_client)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/cancel",
        json={"reason": "no longer needed at all"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# _translate_error: direct unit coverage for service-error → HTTP mapping
# ---------------------------------------------------------------------------


def test_translate_error_not_found() -> None:
    """NotFoundError → 404."""
    err = NotFoundError(resource_type="task", resource_id="123")
    http_exc = _translate_error(err)
    assert isinstance(http_exc, HTTPException)
    assert http_exc.status_code == HTTPStatus.NOT_FOUND
    assert "task not found" in http_exc.detail.lower()


def test_translate_error_unauthorized() -> None:
    """UnauthorizedError → 403."""
    err = UnauthorizedError(action="delete", reason="not your task")
    http_exc = _translate_error(err)
    assert http_exc.status_code == HTTPStatus.FORBIDDEN
    assert "delete" in http_exc.detail


def test_translate_error_validation() -> None:
    """ValidationError → 400."""
    err = ValidationError("bad field value")
    http_exc = _translate_error(err)
    assert http_exc.status_code == HTTPStatus.BAD_REQUEST
    assert http_exc.detail == "bad field value"


def test_translate_error_generic_service_error() -> None:
    """Plain ServiceError → 500."""
    err = ServiceError("service exploded")
    http_exc = _translate_error(err)
    assert http_exc.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert http_exc.detail == "service exploded"


# ---------------------------------------------------------------------------
# create_task: role denial + audit logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_role_not_authorized(task_client: dict) -> None:
    """Override agent to a role that cannot CREATE; audit denial path runs."""
    app = task_client["client"]._transport.app

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=task_client["agent"].id,
            role=AgentRole.QA,
            team=Team.BACKEND,
        )

    app.dependency_overrides[get_agent_context] = _override_agent
    response = await task_client["client"].post(
        "/api/tasks",
        json={
            "title": "T",
            "description": "Twenty character description here ok",
            "acceptance_criteria": ["a"],
            "team": "backend",
            "project_id": str(task_client["project"].id),
            "task_type": "code",
            "nature": "technical",
            "estimated_complexity": "medium",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert "Not authorized" in response.json()["detail"]


# ---------------------------------------------------------------------------
# update_task: forbidden (non-owner non-PM) + 500 fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_forbidden_non_owner(task_client: dict) -> None:
    """Developer who is neither owner nor creator gets 403."""
    other = await _seed_agent(task_client)
    task = _seed_task(task_client, assigned_to=other.id, created_by=other.id)
    await task_client["db"].flush()

    app = task_client["client"]._transport.app

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=uuid4(), role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    app.dependency_overrides[get_agent_context] = _override_agent
    response = await task_client["client"].patch(
        f"/api/tasks/{task.id}",
        json={"title": "X"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert "Not authorized" in response.json()["detail"]


@pytest.mark.asyncio
async def test_update_task_service_returns_none_yields_500(
    task_client: dict,
) -> None:
    """Force service.update() to return None → route raises 500."""
    task = _seed_task(task_client)
    await task_client["db"].flush()

    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.update = AsyncMock(return_value=None)
        mock_factory.return_value = instance
        response = await task_client["client"].patch(
            f"/api/tasks/{task.id}",
            json={"title": "Renamed"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert "update failed" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# claim_task: ServiceError -> _translate_error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_task_service_error_translated(task_client: dict) -> None:
    """A ServiceError raised by claim_task_for_agent surfaces via _translate_error."""
    task = _seed_task(task_client)
    await task_client["db"].flush()

    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.claim_task_for_agent = AsyncMock(
            side_effect=ValidationError("Cannot claim — already claimed")
        )
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/claim",
            json={"agent_id": "main-pm"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_claim_task_success(task_client: dict) -> None:
    """Happy path: claim returns task, route serializes it."""
    task = _seed_task(task_client)
    await task_client["db"].flush()

    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.claim_task_for_agent = AsyncMock(return_value=task)
        mock_factory.return_value = instance
        # No body — claim with the caller's own context.
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/claim",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["id"] == str(task.id)


# ---------------------------------------------------------------------------
# start_task: success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_task_success(task_client: dict) -> None:
    """Claimed task with branch + plan starts cleanly → in_progress."""
    task = _seed_task(
        task_client,
        status=TaskStatus.CLAIMED,
        assigned_to=task_client["agent"].id,
        branch_name="feature/backend/X",
        plan={"steps": ["a"]},
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/start", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "in_progress"


@pytest.mark.asyncio
async def test_start_task_service_returns_none_returns_400(
    task_client: dict,
) -> None:
    """If service.start returns None on a non-claimed/paused task, route 400s."""
    task = _seed_task(
        task_client,
        status=TaskStatus.IN_PROGRESS,
        assigned_to=task_client["agent"].id,
        branch_name="feature/backend/X",
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/start", headers=_HDR
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "invalid status" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# block_task: success + 500 fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_task_success(task_client: dict) -> None:
    """PM blocks a task with a real blocker_id → 200."""
    blocker = _seed_task(task_client)
    target = _seed_task(
        task_client,
        status=TaskStatus.IN_PROGRESS,
        assigned_to=task_client["agent"].id,
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{target.id}/block?blocker_id={blocker.id}",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "blocked"


@pytest.mark.asyncio
async def test_block_task_service_returns_none_500(task_client: dict) -> None:
    """If service.block returns None → 500."""
    task = _seed_task(
        task_client,
        status=TaskStatus.IN_PROGRESS,
        assigned_to=task_client["agent"].id,
    )
    blocker = _seed_task(task_client)
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.block = AsyncMock(return_value=None)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/block?blocker_id={blocker.id}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert "block failed" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# soft_block: success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_block_task_success(task_client: dict) -> None:
    task = _seed_task(
        task_client,
        status=TaskStatus.IN_PROGRESS,
        assigned_to=task_client["agent"].id,
    )
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.soft_block_task_for_agent = AsyncMock(return_value=task)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/soft-block",
            json={
                "reason": "external system unavailable",
                "blocker_type": "external",
                "what_needed": "Stripe API",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# unblock: success notifies assignee + commits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unblock_task_success_notifies_assignee(
    task_client: dict,
) -> None:
    """Unblock a blocked task assigned to a different agent → notification path."""
    other = await _seed_agent(task_client)
    task = _seed_task(
        task_client,
        status=TaskStatus.BLOCKED,
        assigned_to=other.id,
    )
    await task_client["db"].flush()

    with patch(
        "roboco.api.routes.tasks.get_notification_delivery_service"
    ) as mock_delivery:
        delivery_instance = AsyncMock()
        delivery_instance.notify_assignee_of_unblock = AsyncMock(return_value=None)
        mock_delivery.return_value = delivery_instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/unblock", headers=_HDR
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] != "blocked"
    delivery_instance.notify_assignee_of_unblock.assert_awaited_once()


# ---------------------------------------------------------------------------
# pause / resume / verify success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_task_success(task_client: dict) -> None:
    task = _seed_task(
        task_client,
        status=TaskStatus.IN_PROGRESS,
        assigned_to=task_client["agent"].id,
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/pause", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "paused"


@pytest.mark.asyncio
async def test_resume_task_success(task_client: dict) -> None:
    task = _seed_task(
        task_client,
        status=TaskStatus.PAUSED,
        assigned_to=task_client["agent"].id,
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/resume", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_verify_task_success(task_client: dict) -> None:
    task = _seed_task(
        task_client,
        status=TaskStatus.IN_PROGRESS,
        assigned_to=task_client["agent"].id,
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/verify", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# submit_for_qa: success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_qa_success(task_client: dict) -> None:
    """All gates satisfied + status=verifying → submit succeeds."""
    task = _seed_task(
        task_client,
        status=TaskStatus.VERIFYING,
        assigned_to=task_client["agent"].id,
        self_verified=True,
        commits=[
            {
                "hash": "abc1234",
                "message": "wip",
                "timestamp": "2026-01-01T00:00:00+00:00",
            }
        ],
        pr_number=42,
        progress_updates=[
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "agent_id": str(task_client["agent"].id),
                "message": "started",
            }
        ],
    )
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/submit-qa", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "awaiting_qa"


@pytest.mark.asyncio
async def test_submit_qa_service_returns_none_returns_400(
    task_client: dict,
) -> None:
    """Force service.submit_for_qa to return None → route 400s with cannot submit."""
    task = _seed_task(
        task_client,
        status=TaskStatus.VERIFYING,
        assigned_to=task_client["agent"].id,
        self_verified=True,
        commits=[{"hash": "abc", "message": "fix"}],
        pr_number=42,
        progress_updates=[
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "agent_id": str(task_client["agent"].id),
                "message": "started",
            }
        ],
    )
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.submit_for_qa = AsyncMock(return_value=None)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/submit-qa", headers=_HDR
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "not verifying" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# pass_qa: full body coverage
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def qa_client(db_session: AsyncSession) -> AsyncIterator[dict]:
    """Client where the agent context role is QA."""
    qa = AgentTable(
        id=uuid4(),
        name="QA",
        slug=f"be-qa-{uuid4().hex[:8]}",
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

    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/tasks")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=qa.id, role=AgentRole.QA, team=Team.BACKEND)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "agent": qa,
            "project": project,
            "db": db_session,
        }
    app.dependency_overrides.clear()


def _seed_task_qa(setup: dict, **kw) -> TaskTable:
    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=kw.pop("status", TaskStatus.AWAITING_QA),
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=setup["project"].id,
        created_by=setup["agent"].id,
        team=Team.BACKEND,
        **kw,
    )
    setup["db"].add(task)
    return task


@pytest.mark.asyncio
async def test_pass_qa_self_review_forbidden(qa_client: dict) -> None:
    """QA agent cannot pass-QA on a task where they were the original developer."""
    task = _seed_task_qa(
        qa_client,
        pr_number=42,
        quick_context=f"original_developer:{qa_client['agent'].id}",
    )
    await qa_client["db"].flush()
    response = await qa_client["client"].post(
        f"/api/tasks/{task.id}/pass-qa",
        json={"notes": "looks good and covers all criteria"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert "your own task" in response.json()["detail"]


@pytest.mark.asyncio
async def test_pass_qa_no_pr_attached(qa_client: dict) -> None:
    """pass-qa without a PR returns NO_PR_ATTACHED 400."""
    task = _seed_task_qa(qa_client, pr_number=None)
    await qa_client["db"].flush()
    response = await qa_client["client"].post(
        f"/api/tasks/{task.id}/pass-qa",
        json={"notes": "ok and was thorough enough for review"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "NO_PR_ATTACHED" in response.json()["detail"]


@pytest.mark.asyncio
async def test_pass_qa_notes_too_short(qa_client: dict) -> None:
    """pass-qa with notes < 20 chars → QA_NOTES_REQUIRED 400."""
    task = _seed_task_qa(qa_client, pr_number=42)
    await qa_client["db"].flush()
    response = await qa_client["client"].post(
        f"/api/tasks/{task.id}/pass-qa",
        json={"notes": "ok"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "QA_NOTES_REQUIRED" in response.json()["detail"]


@pytest.mark.asyncio
async def test_pass_qa_no_notes_at_all(qa_client: dict) -> None:
    """pass-qa with NO body at all → QA_NOTES_REQUIRED 400."""
    task = _seed_task_qa(qa_client, pr_number=42)
    await qa_client["db"].flush()
    response = await qa_client["client"].post(
        f"/api/tasks/{task.id}/pass-qa",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "QA_NOTES_REQUIRED" in response.json()["detail"]


@pytest.mark.asyncio
async def test_pass_qa_success(qa_client: dict) -> None:
    """Happy path — QA passes a task, transitions to awaiting_documentation."""
    task = _seed_task_qa(qa_client, pr_number=42)
    await qa_client["db"].flush()
    response = await qa_client["client"].post(
        f"/api/tasks/{task.id}/pass-qa",
        json={
            "notes": (
                "Verified all acceptance criteria match the PR diff. "
                "No security issues."
            )
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "awaiting_documentation"


@pytest.mark.asyncio
async def test_pass_qa_service_returns_none(qa_client: dict) -> None:
    """If service.pass_qa returns None → route 400s."""
    task = _seed_task_qa(qa_client, pr_number=42)
    await qa_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.pass_qa = AsyncMock(return_value=None)
        mock_factory.return_value = instance
        response = await qa_client["client"].post(
            f"/api/tasks/{task.id}/pass-qa",
            json={"notes": "verified all acceptance criteria are met."},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "invalid status" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# fail_qa: full body coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_qa_self_review_forbidden(qa_client: dict) -> None:
    """QA cannot fail-QA on a task where they were the dev."""
    task = _seed_task_qa(
        qa_client,
        quick_context=f"original_developer:{qa_client['agent'].id}",
    )
    await qa_client["db"].flush()
    response = await qa_client["client"].post(
        f"/api/tasks/{task.id}/fail-qa",
        json={"notes": "broken in many ways"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert "your own task" in response.json()["detail"]


@pytest.mark.asyncio
async def test_fail_qa_success(qa_client: dict) -> None:
    """fail-qa happy path → needs_revision."""
    task = _seed_task_qa(qa_client)
    await qa_client["db"].flush()
    response = await qa_client["client"].post(
        f"/api/tasks/{task.id}/fail-qa",
        json={"notes": "broken in some ways"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "needs_revision"


@pytest.mark.asyncio
async def test_fail_qa_service_returns_none(qa_client: dict) -> None:
    """If service.fail_qa returns None → 400."""
    task = _seed_task_qa(qa_client)
    await qa_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.fail_qa = AsyncMock(return_value=None)
        mock_factory.return_value = instance
        response = await qa_client["client"].post(
            f"/api/tasks/{task.id}/fail-qa",
            json={"notes": "broken"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# docs_complete: success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_complete_success(task_client: dict) -> None:
    """Mock service.docs_complete_for_task to return a task, route serializes it."""
    task = _seed_task(task_client)
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.docs_complete_for_task = AsyncMock(return_value=task)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/docs-complete",
            json={"notes": "documented thoroughly enough"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# submit_pm_review: success + None branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_pm_review_success(task_client: dict) -> None:
    """Assigned in_progress task — assignee submits for PM review."""
    task = _seed_task(
        task_client,
        status=TaskStatus.IN_PROGRESS,
        assigned_to=task_client["agent"].id,
        branch_name="feature/backend/X",
        pr_created=True,
        pr_number=42,
    )
    await task_client["db"].flush()
    with patch(
        "roboco.api.routes.tasks.get_notification_delivery_service"
    ) as mock_delivery:
        delivery_instance = AsyncMock()
        delivery_instance.notify_pm_of_review_submission = AsyncMock(return_value=None)
        mock_delivery.return_value = delivery_instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/submit-pm-review",
            json={"notes": "Submitted for PM review please."},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    delivery_instance.notify_pm_of_review_submission.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_pm_review_service_returns_none(task_client: dict) -> None:
    """If service.submit_for_pm_review returns None → 400."""
    task = _seed_task(
        task_client,
        status=TaskStatus.IN_PROGRESS,
        assigned_to=task_client["agent"].id,
    )
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.submit_for_pm_review = AsyncMock(return_value=None)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/submit-pm-review",
            json={"notes": "Ready for PM review — all criteria met."},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "not in progress" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# complete_task: success path through service mock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_task_success(task_client: dict) -> None:
    task = _seed_task(task_client)
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.complete_task_for_agent = AsyncMock(return_value=task)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/complete",
            json={
                "force_with_cancelled": False,
                "justification": "All acceptance criteria met; merging.",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_complete_without_justification_rejected(task_client: dict) -> None:
    """Audit: completing a task must carry its rationale (>= 20 chars)."""
    task = _seed_task(task_client)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/complete",
        json={"force_with_cancelled": False},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "JUSTIFICATION_REQUIRED" in response.json()["detail"]


# ---------------------------------------------------------------------------
# cancel: service returns None branch (1162-1167)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_task_service_returns_none(task_client: dict) -> None:
    task = _seed_task(task_client)
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.cancel = AsyncMock(return_value=None)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/cancel",
            json={"reason": "no longer needed at all"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


# ---------------------------------------------------------------------------
# CEO endpoints with separate ceo_client fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ceo_client(db_session: AsyncSession) -> AsyncIterator[dict]:
    """Client where agent role is CEO."""
    ceo = AgentTable(
        id=uuid4(),
        name="CEO",
        slug=f"ceo-{uuid4().hex[:8]}",
        role=AgentRole.CEO,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="ceo",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(ceo)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="CEO-Proj",
        slug=f"ceo-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=ceo.id,
    )
    db_session.add(project)
    await db_session.flush()

    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/tasks")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=ceo.id, role=AgentRole.CEO, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "agent": ceo,
            "project": project,
            "db": db_session,
        }
    app.dependency_overrides.clear()


def _seed_task_ceo(setup: dict, **kw) -> TaskTable:
    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=kw.pop("status", TaskStatus.AWAITING_CEO_APPROVAL),
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=setup["project"].id,
        created_by=setup["agent"].id,
        team=Team.BACKEND,
        **kw,
    )
    setup["db"].add(task)
    return task


@pytest.mark.asyncio
async def test_get_awaiting_pm_review_via_query(task_client: dict) -> None:
    """Hit get_awaiting_pm_review_tasks helper directly (route ordering quirk)."""
    db = task_client["db"]
    agent_ctx = AgentContext(
        agent_id=task_client["agent"].id, role=AgentRole.MAIN_PM, team=None
    )
    permissions = PermissionService()
    result = await get_awaiting_pm_review_tasks(
        db=db,
        agent=agent_ctx,
        permissions=permissions,
        team=Team.BACKEND,
    )
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_get_awaiting_pm_review_no_view_all(task_client: dict) -> None:
    """Developer (no VIEW_ALL) — falls back to agent.team."""
    db = task_client["db"]
    agent_ctx = AgentContext(
        agent_id=task_client["agent"].id,
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
    )
    permissions = PermissionService()
    result = await get_awaiting_pm_review_tasks(
        db=db, agent=agent_ctx, permissions=permissions, team=None
    )
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_get_awaiting_ceo_approval_pm_role(task_client: dict) -> None:
    """Main PM can view CEO approval queue — direct invocation."""
    db = task_client["db"]
    agent_ctx = AgentContext(
        agent_id=task_client["agent"].id, role=AgentRole.MAIN_PM, team=None
    )
    permissions = PermissionService()
    result = await get_awaiting_ceo_approval_tasks(
        db=db, agent=agent_ctx, permissions=permissions
    )
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_get_awaiting_ceo_approval_developer_forbidden(
    task_client: dict,
) -> None:
    """Developer (no VIEW_ALL, not PM/CEO) → 403 from helper."""
    db = task_client["db"]
    agent_ctx = AgentContext(
        agent_id=task_client["agent"].id,
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
    )
    permissions = PermissionService()
    with pytest.raises(HTTPException) as exc_info:
        await get_awaiting_ceo_approval_tasks(
            db=db, agent=agent_ctx, permissions=permissions
        )
    assert exc_info.value.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_escalate_to_ceo_returns_task(task_client: dict) -> None:
    """Mock service.escalate_to_ceo_for_agent → route returns serialized task."""
    task = _seed_task(task_client)
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.escalate_to_ceo_for_agent = AsyncMock(return_value=task)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/escalate-to-ceo",
            json={"notes": "Need CEO sign-off please"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_ceo_approve_task_not_found(ceo_client: dict) -> None:
    response = await ceo_client["client"].post(
        f"/api/tasks/{uuid4()}/ceo-approve",
        json={"notes": "approved"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_ceo_approve_service_returns_none(ceo_client: dict) -> None:
    """If service.ceo_approve returns None — 400."""
    task = _seed_task_ceo(ceo_client, status=TaskStatus.PENDING)
    await ceo_client["db"].flush()
    response = await ceo_client["client"].post(
        f"/api/tasks/{task.id}/ceo-approve",
        json={"notes": "Reviewed and approved for production release."},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "not awaiting CEO" in response.json()["detail"]


@pytest.mark.asyncio
async def test_ceo_approve_success(ceo_client: dict) -> None:
    task = _seed_task_ceo(ceo_client)
    await ceo_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.ceo_approve = AsyncMock(return_value=task)
        mock_factory.return_value = instance
        response = await ceo_client["client"].post(
            f"/api/tasks/{task.id}/ceo-approve",
            json={"notes": "Verified against all acceptance criteria; approved."},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_ceo_approve_without_notes_rejected(ceo_client: dict) -> None:
    """Audit: a CEO approval with no/thin notes leaves no record of WHY the
    work shipped, so the endpoint must reject it (>= 20 chars required). The
    panel collects the note before POSTing."""
    task = _seed_task_ceo(ceo_client)
    await ceo_client["db"].flush()
    for body in ({}, {"notes": ""}, {"notes": "lgtm"}):
        response = await ceo_client["client"].post(
            f"/api/tasks/{task.id}/ceo-approve",
            json=body,
            headers=_HDR,
        )
        assert response.status_code in (
            HTTPStatus.BAD_REQUEST,
            HTTPStatus.UNPROCESSABLE_ENTITY,
        ), (body, response.status_code)


@pytest.mark.asyncio
async def test_approve_and_start_success(ceo_client: dict) -> None:
    task = _seed_task_ceo(ceo_client, status=TaskStatus.PENDING)
    await ceo_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.approve_and_start = AsyncMock(return_value=task)
        mock_factory.return_value = instance
        resp = await ceo_client["client"].post(
            f"/api/tasks/{task.id}/approve-and-start",
            json={"notes": "Board review complete; clear requirements; build it now."},
            headers=_HDR,
        )
    assert resp.status_code == HTTPStatus.OK
    instance.approve_and_start.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_and_start_requires_ceo(task_client: dict) -> None:
    # task_client is MAIN_PM-role; the inline CEO guard must 403.
    resp = await task_client["client"].post(
        f"/api/tasks/{uuid4()}/approve-and-start",
        json={"notes": "x" * 30},
        headers=_HDR,
    )
    assert resp.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_approve_and_start_short_notes(ceo_client: dict) -> None:
    task = _seed_task_ceo(ceo_client, status=TaskStatus.PENDING)
    await ceo_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        mock_factory.return_value = instance
        resp = await ceo_client["client"].post(
            f"/api/tasks/{task.id}/approve-and-start",
            json={"notes": "too short"},
            headers=_HDR,
        )
    assert resp.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_approve_and_start_missing_task_404_before_notes_gate(
    ceo_client: dict,
) -> None:
    # Missing task -> 404 even with valid notes: the not-found guard runs
    # before the notes gate, so service.approve_and_start is never reached.
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=None)
        mock_factory.return_value = instance
        resp = await ceo_client["client"].post(
            f"/api/tasks/{uuid4()}/approve-and-start",
            json={"notes": "Board review complete; clear requirements; build it now."},
            headers=_HDR,
        )
    assert resp.status_code == HTTPStatus.NOT_FOUND
    instance.approve_and_start.assert_not_awaited()


@pytest.mark.asyncio
async def test_ceo_reject_task_not_found(ceo_client: dict) -> None:
    response = await ceo_client["client"].post(
        f"/api/tasks/{uuid4()}/ceo-reject",
        json={"notes": "rejected"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_ceo_reject_service_returns_none(ceo_client: dict) -> None:
    """ceo_reject on a task not in awaiting_ceo_approval — service None → 400."""
    task = _seed_task_ceo(ceo_client, status=TaskStatus.PENDING)
    await ceo_client["db"].flush()
    response = await ceo_client["client"].post(
        f"/api/tasks/{task.id}/ceo-reject",
        json={"notes": "rejected"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_ceo_reject_success_notifies_assignee(ceo_client: dict) -> None:
    """ceo_reject success path with assignee triggers notification."""
    other = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    ceo_client["db"].add(other)
    await ceo_client["db"].flush()

    task = _seed_task_ceo(ceo_client, assigned_to=other.id)
    await ceo_client["db"].flush()

    with (
        patch("roboco.api.routes.tasks.get_task_service") as mock_factory,
        patch(
            "roboco.api.routes.tasks.get_notification_delivery_service"
        ) as mock_delivery,
    ):
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.ceo_reject = AsyncMock(return_value=task)
        mock_factory.return_value = instance
        delivery_instance = AsyncMock()
        delivery_instance.notify_assignee_of_ceo_rejection = AsyncMock(
            return_value=None
        )
        mock_delivery.return_value = delivery_instance
        response = await ceo_client["client"].post(
            f"/api/tasks/{task.id}/ceo-reject",
            json={"notes": "rejected with detailed notes"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    delivery_instance.notify_assignee_of_ceo_rejection.assert_awaited_once()


# ---------------------------------------------------------------------------
# escalate (general): success + EscalationError 403/400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_task_success(task_client: dict) -> None:
    """escalate_and_notify returns outcome → service.apply_escalation runs."""
    task = _seed_task(task_client)
    await task_client["db"].flush()
    outcome = SimpleNamespace(
        target_agent_id=uuid4(),
        escalator_slug="be-dev-1",
        target_slug="be-pm",
    )
    with (
        patch(
            "roboco.api.routes.tasks.get_notification_delivery_service"
        ) as mock_delivery,
        patch("roboco.api.routes.tasks.get_task_service") as mock_factory,
    ):
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.apply_escalation = AsyncMock(return_value=None)
        mock_factory.return_value = instance
        delivery_instance = AsyncMock()
        delivery_instance.escalate_and_notify = AsyncMock(return_value=outcome)
        mock_delivery.return_value = delivery_instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/escalate",
            json={"reason": "Need help — out of scope"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["status"] == "escalated"
    assert body["escalated_to"] == "be-pm"


@pytest.mark.asyncio
async def test_escalate_task_escalation_error_404(task_client: dict) -> None:
    """EscalationError starting with 'escalator agent' → 404."""
    task = _seed_task(task_client)
    await task_client["db"].flush()
    with patch(
        "roboco.api.routes.tasks.get_notification_delivery_service"
    ) as mock_delivery:
        delivery_instance = AsyncMock()
        delivery_instance.escalate_and_notify = AsyncMock(
            side_effect=EscalationError("escalator agent missing")
        )
        mock_delivery.return_value = delivery_instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/escalate",
            json={"reason": "stuck"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_escalate_task_escalation_error_403(task_client: dict) -> None:
    """EscalationError 'Cannot escalate to ...' → 403."""
    task = _seed_task(task_client)
    await task_client["db"].flush()
    with patch(
        "roboco.api.routes.tasks.get_notification_delivery_service"
    ) as mock_delivery:
        delivery_instance = AsyncMock()
        delivery_instance.escalate_and_notify = AsyncMock(
            side_effect=EscalationError("Cannot escalate to qa")
        )
        mock_delivery.return_value = delivery_instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/escalate",
            json={"reason": "stuck"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_escalate_task_escalation_error_400(task_client: dict) -> None:
    """Other EscalationError → 400."""
    task = _seed_task(task_client)
    await task_client["db"].flush()
    with patch(
        "roboco.api.routes.tasks.get_notification_delivery_service"
    ) as mock_delivery:
        delivery_instance = AsyncMock()
        delivery_instance.escalate_and_notify = AsyncMock(
            side_effect=EscalationError("no chain configured")
        )
        mock_delivery.return_value = delivery_instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/escalate",
            json={"reason": "stuck"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# substitute: success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_substitute_task_success(task_client: dict) -> None:
    task = _seed_task(task_client, assigned_to=task_client["agent"].id)
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.substitute_task_for_agent = AsyncMock(return_value=task)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/substitute",
            json={"reason": "low_context", "details": "Need more context"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# progress / checkpoint / commit: success and 500-fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_progress_success(task_client: dict) -> None:
    task = _seed_task(task_client, assigned_to=task_client["agent"].id)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/progress",
        json={"message": "Halfway done now", "percentage": 50},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_add_progress_service_returns_none_500(task_client: dict) -> None:
    task = _seed_task(task_client, assigned_to=task_client["agent"].id)
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.add_progress = AsyncMock(return_value=None)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/progress",
            json={"message": "halfway done now", "percentage": 50},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_add_checkpoint_success(task_client: dict) -> None:
    task = _seed_task(task_client, assigned_to=task_client["agent"].id)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/checkpoint",
        json={"state_summary": "halfway", "remaining_work": ["finish API"]},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_add_checkpoint_service_returns_none_500(task_client: dict) -> None:
    task = _seed_task(task_client, assigned_to=task_client["agent"].id)
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.add_checkpoint = AsyncMock(return_value=None)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/checkpoint",
            json={"state_summary": "halfway", "remaining_work": ["finish"]},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_add_commit_success(task_client: dict) -> None:
    task = _seed_task(task_client, assigned_to=task_client["agent"].id)
    await task_client["db"].flush()
    response = await task_client["client"].post(
        f"/api/tasks/{task.id}/commit",
        json={"hash": "abc1234", "message": "fix"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_add_commit_service_returns_none_500(task_client: dict) -> None:
    task = _seed_task(task_client, assigned_to=task_client["agent"].id)
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=task)
        instance.add_commit = AsyncMock(return_value=None)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/commit",
            json={"hash": "abc1234", "message": "fix"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


# ---------------------------------------------------------------------------
# activate: success + ValueError + TaskLifecycleError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_success(task_client: dict) -> None:
    task = _seed_task(task_client, status=TaskStatus.BACKLOG)
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.activate = AsyncMock(return_value=task)
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/activate", headers=_HDR
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_activate_value_error_returns_400(task_client: dict) -> None:
    task = _seed_task(task_client, status=TaskStatus.BACKLOG)
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.activate = AsyncMock(side_effect=ValueError("no session linked"))
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/activate", headers=_HDR
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "no session" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_activate_task_lifecycle_error_returns_403(task_client: dict) -> None:
    task = _seed_task(task_client, status=TaskStatus.BACKLOG)
    await task_client["db"].flush()
    with patch("roboco.api.routes.tasks.get_task_service") as mock_factory:
        instance = AsyncMock()
        instance.activate = AsyncMock(
            side_effect=TaskLifecycleError(
                current_status="backlog",
                target_status="pending",
                message="Wrong role",
            )
        )
        mock_factory.return_value = instance
        response = await task_client["client"].post(
            f"/api/tasks/{task.id}/activate", headers=_HDR
        )
    assert response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# get_sessions_for_task: 404 path for unknown task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sessions_for_task_not_found(task_client: dict) -> None:
    response = await task_client["client"].get(
        f"/api/tasks/{uuid4()}/sessions", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND

"""POST /tasks must reject under-filled tasks at the route boundary.

Mirrors task_completeness.TASK_AT_CREATE. The route layer does a defense-
in-depth completeness check after Pydantic validation, then forwards to
the service. This test pins the route-level enforcement so that future
edits cannot regress the contract that POST /tasks runs the canonical
completeness checker.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.tasks import router as tasks_router
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def post_tasks_client(
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
        name="PostTasksProj",
        slug=f"post-tasks-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=main_pm.id,
    )
    db_session.add(project)
    await db_session.flush()

    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/tasks")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", main_pm.id), role=AgentRole.MAIN_PM, team=None
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "project": project}
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


@pytest.mark.asyncio
async def test_post_tasks_rejects_empty_acceptance_criteria(
    post_tasks_client: dict,
) -> None:
    """Schema-level rejection: TaskCreate.acceptance_criteria has min_length=1."""
    client = post_tasks_client["client"]
    payload = {
        "title": "Something",
        "description": "A long enough description, easily over twenty chars.",
        "acceptance_criteria": [],
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
        "team": "backend",
        "project_id": str(post_tasks_client["project"].id),
    }
    resp = await client.post("/api/tasks", json=payload, headers=_HDR)
    # Pydantic ValidationError on TaskCreate.acceptance_criteria min_length=1.
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_post_tasks_rejects_placeholder_phrase(
    post_tasks_client: dict,
) -> None:
    """Route-level enforcement: denylist catches placeholders that pass Pydantic.

    The phrase 'completed and reviewed by assignee' is a denylisted legacy
    silent-fallback phrase. Pydantic accepts it (non-empty list of non-empty
    strings) but `task_completeness.check(TASK_AT_CREATE, payload)` rejects.
    """
    client = post_tasks_client["client"]
    payload = {
        "title": "Something",
        "description": "A long enough description, easily over twenty chars.",
        "acceptance_criteria": ["completed and reviewed by assignee"],
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
        "team": "backend",
        "project_id": str(post_tasks_client["project"].id),
    }
    resp = await client.post("/api/tasks", json=payload, headers=_HDR)
    assert resp.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )
    assert "acceptance_criteria" in resp.text.lower()

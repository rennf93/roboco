"""Privileged-field gate on PATCH /api/tasks/{id} — the structural/ownership
fields (assigned_to/team/parent_task_id/...) are PM-gated; a bare task owner
(UPDATE_OWN) must not self-edit them past the verb layer."""

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
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def dev_client(db_session: AsyncSession) -> AsyncIterator[dict]:
    """A developer owner (UPDATE_OWN, no ASSIGN) acting on its own task."""
    dev = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(dev)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="PF-Proj",
        slug=f"pf-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/pf.git",
        assigned_cell=Team.BACKEND,
        created_by=dev.id,
    )
    db_session.add(project)
    await db_session.flush()

    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/tasks")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", dev.id), role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "agent": dev, "project": project, "db": db_session}
    app.dependency_overrides.clear()


def _seed_owned(setup: dict, **kw) -> TaskTable:
    task = TaskTable(
        id=uuid4(),
        title=kw.pop("title", "t"),
        description=kw.pop("description", "d"),
        acceptance_criteria=["ac"],
        status=kw.pop("status", TaskStatus.IN_PROGRESS),
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=setup["project"].id,
        created_by=setup["agent"].id,
        assigned_to=setup["agent"].id,
        team=Team.BACKEND,
    )
    setup["db"].add(task)
    return task


_HDR = {"X-Agent-ID": "ignored", "X-Agent-Role": "developer"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("assigned_to", str(uuid4())),
        ("team", "frontend"),
        ("parent_task_id", str(uuid4())),
    ],
)
async def test_owner_cannot_patch_privileged_fields(
    dev_client: dict, field: str, value: object
) -> None:
    """A developer owner (UPDATE_OWN, no ASSIGN) cannot self-reassign / re-team
    / re-parent their task — those are PM-gated; the REST surface must not
    bypass the verb layer's reassign/delegate/triage gate."""
    client = dev_client["client"]
    task = _seed_owned(dev_client)
    await dev_client["db"].flush()
    response = await client.patch(
        f"/api/tasks/{task.id}",
        json={field: value},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_owner_can_patch_dev_facing_field(dev_client: dict) -> None:
    """A developer owner may still edit dev-facing fields (description)."""
    client = dev_client["client"]
    task = _seed_owned(dev_client)
    await dev_client["db"].flush()
    response = await client.patch(
        f"/api/tasks/{task.id}",
        json={"description": "a long enough updated description for the schema"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK

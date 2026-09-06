"""GET /api/tasks/{id}/attestation — the per-task verification attestation
export route.

Mirrors test_task_findings_route.py's fixture shape. Covers both output
formats (json, the format=md alias for markdown) plus the 404 path.
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
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def attestation_client(db_session: AsyncSession) -> AsyncIterator[dict]:
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"pm-{uuid4().hex[:8]}",
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
        name="TA-Proj",
        slug=f"ta-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/ta.git",
        assigned_cell=Team.BACKEND,
        created_by=pm.id,
    )
    db_session.add(project)
    await db_session.flush()

    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/tasks")

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
        yield {"client": client, "agent": pm, "project": project, "db": db_session}
    app.dependency_overrides.clear()


def _seed_task(setup: dict) -> TaskTable:
    task = TaskTable(
        id=uuid4(),
        title="Attested route task",
        description="d",
        acceptance_criteria=["works"],
        status=TaskStatus.AWAITING_QA,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=setup["project"].id,
        created_by=setup["agent"].id,
        team=Team.BACKEND,
    )
    setup["db"].add(task)
    return task


_HDR = {"X-Agent-ID": "ignored", "X-Agent-Role": "main_pm"}


@pytest.mark.asyncio
async def test_attestation_404_for_missing_task(attestation_client: dict) -> None:
    client = attestation_client["client"]
    response = await client.get(f"/api/tasks/{uuid4()}/attestation", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_attestation_json_default_format(attestation_client: dict) -> None:
    client = attestation_client["client"]
    task = _seed_task(attestation_client)
    await attestation_client["db"].flush()

    response = await client.get(f"/api/tasks/{task.id}/attestation", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["task_id"] == str(task.id)
    assert body["title"] == "Attested route task"
    assert body["ci"]["state"] == "not_available"


@pytest.mark.asyncio
async def test_attestation_format_markdown(attestation_client: dict) -> None:
    client = attestation_client["client"]
    task = _seed_task(attestation_client)
    await attestation_client["db"].flush()

    response = await client.get(
        f"/api/tasks/{task.id}/attestation", params={"format": "markdown"}, headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK
    assert "text/markdown" in response.headers["content-type"]
    assert "# Verification attestation" in response.text


@pytest.mark.asyncio
async def test_attestation_format_md_alias_matches_markdown(
    attestation_client: dict,
) -> None:
    """``?format=md`` is the spec-compliant shorthand the frontend cell's
    download action uses — it must render the same report as
    ``?format=markdown``, not 422."""
    client = attestation_client["client"]
    task = _seed_task(attestation_client)
    await attestation_client["db"].flush()

    md_response = await client.get(
        f"/api/tasks/{task.id}/attestation", params={"format": "md"}, headers=_HDR
    )
    markdown_response = await client.get(
        f"/api/tasks/{task.id}/attestation", params={"format": "markdown"}, headers=_HDR
    )
    assert md_response.status_code == HTTPStatus.OK
    assert "text/markdown" in md_response.headers["content-type"]
    assert md_response.text == markdown_response.text

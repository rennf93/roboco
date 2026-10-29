"""The Entire trails bridge's CEO surface: the token (write-only, encrypted
at rest), the bridge's status, and the retry of stopped operations."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.entire import router as entire_router
from roboco.db.tables import (
    AgentTable,
    EntireCredentialsTable,
    EntireTrailOutboxTable,
    EntireTrailTable,
    ProjectTable,
    TaskTable,
)
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_TOKEN = "entire-route-test-token"


@pytest_asyncio.fixture
async def api(db_session: AsyncSession) -> AsyncIterator[dict[str, Any]]:
    role = {"value": AgentRole.CEO}
    app = FastAPI()
    app.include_router(entire_router, prefix="/api/entire")

    async def _db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _agent() -> AgentContext:
        return AgentContext(agent_id=uuid4(), role=role["value"], team=None)

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_agent_context] = _agent
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "role": role, "db": db_session}
    app.dependency_overrides.clear()


async def test_the_token_is_stored_encrypted_and_never_returned(
    api: dict[str, Any],
) -> None:
    client: AsyncClient = api["client"]
    assert (await client.get("/api/entire/credentials")).json() == {"has_token": False}

    resp = await client.post("/api/entire/credentials", json={"token": _TOKEN})

    assert resp.json() == {"has_token": True}
    assert _TOKEN not in resp.text
    row = (await api["db"].execute(select(EntireCredentialsTable))).scalar_one()
    assert row.token_encrypted and _TOKEN not in row.token_encrypted
    assert (await client.get("/api/entire/credentials")).json() == {"has_token": True}


async def test_an_empty_token_clears_it(api: dict[str, Any]) -> None:
    client: AsyncClient = api["client"]
    await client.post("/api/entire/credentials", json={"token": _TOKEN})

    resp = await client.post("/api/entire/credentials", json={"token": ""})

    assert resp.json() == {"has_token": False}
    assert (await api["db"].execute(select(EntireCredentialsTable))).first() is None


async def test_only_the_ceo_reaches_the_surface(api: dict[str, Any]) -> None:
    client: AsyncClient = api["client"]
    api["role"]["value"] = AgentRole.MAIN_PM

    for method, path, body in (
        ("get", "/api/entire/credentials", None),
        ("post", "/api/entire/credentials", {"token": _TOKEN}),
        ("get", "/api/entire/trails/status", None),
        ("post", "/api/entire/trails/retry", None),
    ):
        resp = await client.request(method, path, json=body)
        assert resp.status_code == HTTPStatus.FORBIDDEN, path


async def test_status_reports_the_queue_and_retry_revives_stopped_rows(
    api: dict[str, Any],
) -> None:
    db: AsyncSession = api["db"]
    agent = AgentTable(
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
    db.add(agent)
    await db.flush()
    project = ProjectTable(
        id=uuid4(),
        name="App",
        slug=f"app-{uuid4().hex[:6]}",
        git_url="https://github.com/acme/app.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db.add(project)
    await db.flush()
    root = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.IN_PROGRESS,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=agent.id,
        team=Team.MAIN_PM,
    )
    db.add(root)
    await db.flush()
    db.add(EntireTrailTable(root_task_id=root.id, entire_project="gh/acme"))
    await db.flush()
    db.add(EntireTrailOutboxTable(root_task_id=root.id, op="sync_trail", payload={}))
    db.add(
        EntireTrailOutboxTable(
            root_task_id=root.id,
            op="link",
            payload={},
            attempts=8,
            dead=True,
            last_error="API error: Change already belongs to another Trail",
        )
    )
    await db.flush()
    client: AsyncClient = api["client"]

    status = (await client.get("/api/entire/trails/status")).json()

    assert status["open_trails"] == 1
    assert (status["pending"], status["dead"]) == (1, 1)
    assert "another Trail" in status["last_error"]
    assert status["has_token"] is False
    retried = await client.post("/api/entire/trails/retry")
    assert retried.json() == {"retried": 1}
    status = (await client.get("/api/entire/trails/status")).json()
    assert (status["pending"], status["dead"]) == (2, 0)

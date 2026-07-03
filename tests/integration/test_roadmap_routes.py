"""Board roadmap engine route coverage — CEO-only list/approve/reject."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.roadmap import router as roadmap_router
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext
from roboco.services.task import ROADMAP_SOURCE

CEO_UUID = _foundation.AGENTS["ceo"].uuid

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_agent(session: AsyncSession, role: AgentRole, slug: str) -> AgentTable:
    agent = AgentTable(
        id=uuid4(),
        name=slug,
        slug=f"{slug}-{uuid4().hex[:6]}",
        role=role,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(agent)
    await session.flush()
    return agent


async def _seed_ceo(session: AsyncSession) -> None:
    """The CEO row matching ``CEO_UUID`` — approving an item stamps this id as
    the materialized task's ``created_by``, which has an FK to ``agents``."""
    if await session.get(AgentTable, CEO_UUID) is not None:
        return
    session.add(
        AgentTable(
            id=CEO_UUID,
            name="ceo",
            slug=f"ceo-{uuid4().hex[:6]}",
            role=AgentRole.CEO,
            team=None,
            status=AgentStatus.ACTIVE,
            model_config={},
            system_prompt="x",
            capabilities=[],
            permissions={},
            metrics={},
        )
    )
    await session.flush()


async def _seed_cycle(session: AsyncSession) -> tuple[TaskTable, ProjectTable]:
    system = await _seed_agent(session, AgentRole.SYSTEM, "system")
    po = await _seed_agent(session, AgentRole.PRODUCT_OWNER, "product-owner")
    await _seed_ceo(session)
    project = ProjectTable(
        id=uuid4(),
        name="Backend Service",
        slug=f"backend-svc-{uuid4().hex[:6]}",
        git_url="https://example.com/backend-svc.git",
        assigned_cell=Team.BACKEND,
        created_by=system.id,
    )
    session.add(project)
    await session.flush()
    task = TaskTable(
        id=uuid4(),
        title="Roadmap exploration cycle",
        description="Explore and propose a themed cycle of roadmap items.",
        acceptance_criteria=["propose_roadmap() called once"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=system.id,
        assigned_to=po.id,
        team=Team.BOARD,
        source=ROADMAP_SOURCE,
        confirmed_by_human=False,
    )
    session.add(task)
    await session.flush()
    markers.set_roadmap_cycle(
        task,
        {
            "goal": "Close onboarding friction",
            "items": [
                {
                    "id": "item-0",
                    "title": "Streamline signup",
                    "description": "Cut the signup form from 8 fields to 3",
                    "acceptance_criteria": ["signup takes < 30s", "conversion up"],
                    "project_slug": project.slug,
                    "team": "backend",
                    "priority": 2,
                    "rationale": "signup drop-off is the top funnel leak",
                    "status": "proposed",
                    "reject_reason": None,
                    "materialized_task_id": None,
                }
            ],
        },
    )
    await session.flush()
    return task, project


def _build_app(db_session: AsyncSession, role: AgentRole, agent_id: UUID) -> FastAPI:
    app = FastAPI()
    app.include_router(roadmap_router, prefix="/api/roadmap")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=agent_id, role=role, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    return app


@pytest_asyncio.fixture
async def ceo_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """CEO-authed client. Approving an item materializes a new task stamping
    ``agent.agent_id`` as ``created_by`` (an FK to ``agents``), so this must be
    a real seeded row — ``CEO_UUID`` — not an arbitrary ``uuid4()``."""
    await _seed_ceo(db_session)
    app = _build_app(db_session, AgentRole.CEO, CEO_UUID)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_cycles_returns_authored_cycle(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task, _project = await _seed_cycle(db_session)
    resp = await ceo_client.get("/api/roadmap/cycles")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert len(body) == 1
    assert body[0]["task_id"] == str(task.id)
    assert body[0]["goal"] == "Close onboarding friction"
    assert len(body[0]["items"]) == 1


@pytest.mark.asyncio
async def test_approve_item_materializes_backlog_task(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task, _project = await _seed_cycle(db_session)
    resp = await ceo_client.post(f"/api/roadmap/cycles/{task.id}/items/item-0/approve")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "approved"
    assert body["materialized_task_id"] is not None

    materialized = await db_session.get(TaskTable, UUID(body["materialized_task_id"]))
    assert materialized is not None
    assert materialized.status == TaskStatus.BACKLOG


@pytest.mark.asyncio
async def test_approve_unknown_item_is_404(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task, _project = await _seed_cycle(db_session)
    resp = await ceo_client.post(
        f"/api/roadmap/cycles/{task.id}/items/no-such-item/approve"
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_reject_item_records_reason(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task, _project = await _seed_cycle(db_session)
    resp = await ceo_client.post(
        f"/api/roadmap/cycles/{task.id}/items/item-0/reject",
        json={"reason": "not a priority this quarter"},
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "rejected"

    refreshed = await db_session.get(TaskTable, task.id)
    assert refreshed is not None
    payload = markers.get_roadmap_cycle(refreshed)
    assert payload is not None
    assert payload["items"][0]["reject_reason"] == "not a priority this quarter"


@pytest.mark.asyncio
async def test_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    await _seed_cycle(db_session)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        list_resp = await client.get("/api/roadmap/cycles")
    assert list_resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()

"""Scales (Board Program) engine route coverage — CEO-only list/approve/
reject. Approving EXECUTES the item against the live target task. Mirrors
test_pest_control_routes.py."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.scales import router as scales_router
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext
from roboco.services.task import SCALES_SOURCE

CEO_UUID = _foundation.AGENTS["ceo"].uuid
_UNTOUCHED_PRIORITY = 2

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
    """The CEO row matching ``CEO_UUID`` — approving an item stamps this id
    as the actor on the audit row."""
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


async def _seed_cycle(session: AsyncSession) -> tuple[TaskTable, TaskTable]:
    """Returns (exploration cycle task, live target task the item mutates)."""
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
    target = TaskTable(
        id=uuid4(),
        title="Stale onboarding polish",
        description="A live backlog task the rebalance plan targets",
        acceptance_criteria=["done"],
        status=TaskStatus.BACKLOG,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=system.id,
        project_id=project.id,
        team=Team.BACKEND,
    )
    session.add(target)
    await session.flush()
    task = TaskTable(
        id=uuid4(),
        title="Scales portfolio-rebalance cycle",
        description="Review the live backlog and propose a rebalance.",
        acceptance_criteria=["propose_rebalance() called once"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=system.id,
        assigned_to=po.id,
        team=Team.BOARD,
        source=SCALES_SOURCE,
        confirmed_by_human=False,
    )
    session.add(task)
    await session.flush()
    markers.set_rebalance_plan(
        task,
        {
            "items": [
                {
                    "id": "item-0",
                    "task_ref": str(target.id)[:8],
                    "target_task_id": str(target.id),
                    "target_task_title": target.title,
                    "action": "reprioritize",
                    "new_priority": 0,
                    "rationale": (
                        "Onboarding friction is this quarter's top charter goal"
                    ),
                    "status": "proposed",
                    "reject_reason": None,
                    "executed_detail": None,
                }
            ],
        },
    )
    await session.flush()
    return task, target


def _build_app(db_session: AsyncSession, role: AgentRole, agent_id: UUID) -> FastAPI:
    app = FastAPI()
    app.include_router(scales_router, prefix="/api/scales")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=agent_id, role=role, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    return app


@pytest_asyncio.fixture
async def ceo_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
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
    task, _target = await _seed_cycle(db_session)
    resp = await ceo_client.get("/api/scales/cycles")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert len(body) == 1
    assert body[0]["task_id"] == str(task.id)
    assert len(body[0]["items"]) == 1
    assert body[0]["items"][0]["rationale"]


@pytest.mark.asyncio
async def test_approve_reprioritize_item_changes_target_priority(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task, target = await _seed_cycle(db_session)
    resp = await ceo_client.post(f"/api/scales/cycles/{task.id}/items/item-0/approve")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "approved"
    assert body["executed_detail"] == "priority changed to P0"

    refreshed_target = await db_session.get(TaskTable, target.id)
    assert refreshed_target is not None
    assert refreshed_target.priority == 0


@pytest.mark.asyncio
async def test_approve_cancel_item_cancels_target(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    system = await _seed_agent(db_session, AgentRole.SYSTEM, "system2")
    po = await _seed_agent(db_session, AgentRole.PRODUCT_OWNER, "po2")
    project = ProjectTable(
        id=uuid4(),
        name="Frontend App",
        slug=f"frontend-app-{uuid4().hex[:6]}",
        git_url="https://example.com/frontend-app.git",
        assigned_cell=Team.FRONTEND,
        created_by=system.id,
    )
    db_session.add(project)
    await db_session.flush()
    target = TaskTable(
        id=uuid4(),
        title="Dead weight experiment",
        description="A live backlog task the rebalance plan targets",
        acceptance_criteria=["done"],
        status=TaskStatus.BACKLOG,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=system.id,
        project_id=project.id,
        team=Team.FRONTEND,
    )
    db_session.add(target)
    await db_session.flush()
    task = TaskTable(
        id=uuid4(),
        title="Scales portfolio-rebalance cycle",
        description="Review the live backlog and propose a rebalance.",
        acceptance_criteria=["propose_rebalance() called once"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=system.id,
        assigned_to=po.id,
        team=Team.BOARD,
        source=SCALES_SOURCE,
        confirmed_by_human=False,
    )
    db_session.add(task)
    await db_session.flush()
    markers.set_rebalance_plan(
        task,
        {
            "items": [
                {
                    "id": "item-0",
                    "task_ref": str(target.id)[:8],
                    "target_task_id": str(target.id),
                    "target_task_title": target.title,
                    "action": "cancel",
                    "new_priority": None,
                    "rationale": (
                        "Superseded by the new dashboard; no longer on the roadmap"
                    ),
                    "status": "proposed",
                    "reject_reason": None,
                    "executed_detail": None,
                }
            ],
        },
    )
    await db_session.flush()

    resp = await ceo_client.post(f"/api/scales/cycles/{task.id}/items/item-0/approve")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "approved"
    assert body["executed_detail"] == "cancelled"

    refreshed_target = await db_session.get(TaskTable, target.id)
    assert refreshed_target is not None
    assert refreshed_target.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_approve_unknown_item_is_404(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task, _target = await _seed_cycle(db_session)
    resp = await ceo_client.post(
        f"/api/scales/cycles/{task.id}/items/no-such-item/approve"
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_reject_item_records_reason(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task, target = await _seed_cycle(db_session)
    resp = await ceo_client.post(
        f"/api/scales/cycles/{task.id}/items/item-0/reject",
        json={"reason": "still worth doing at current priority"},
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "rejected"

    refreshed = await db_session.get(TaskTable, task.id)
    assert refreshed is not None
    payload = markers.get_rebalance_plan(refreshed)
    assert payload is not None
    assert (
        payload["items"][0]["reject_reason"] == "still worth doing at current priority"
    )

    refreshed_target = await db_session.get(TaskTable, target.id)
    assert refreshed_target is not None
    assert refreshed_target.priority == _UNTOUCHED_PRIORITY  # untouched


@pytest.mark.asyncio
async def test_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    await _seed_cycle(db_session)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        list_resp = await client.get("/api/scales/cycles")
    assert list_resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()

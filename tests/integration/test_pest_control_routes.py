"""Pest Control (Board Program) engine route coverage — CEO-only
list/approve/reject. Mirrors test_roadmap_routes.py."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.pest_control import router as pest_control_router
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext
from roboco.services.task import PEST_CONTROL_SOURCE
from sqlalchemy import update

_SEED_GIT_URL = "https://example.com/backend-svc.git"

CEO_UUID = _foundation.AGENTS["ceo"].uuid
MAIN_PM_UUID = _foundation.AGENTS["main-pm"].uuid

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


async def _seed_main_pm(session: AsyncSession) -> None:
    """The Main PM row matching ``MAIN_PM_UUID`` — approving an item now
    assigns this id to the materialized task (an FK to ``agents``). The
    slug must be the exact ``"main-pm"`` (not a randomized suffix like the
    other seed helpers here use): ``TaskService.approve_and_start`` and
    other call sites resolve the Main PM by an EXACT slug lookup, and this
    row's id is the fixed, cross-test-shared foundation UUID — a wrong slug
    here would permanently squat that id with an unresolvable row for every
    other test in the shared suite run."""
    if await session.get(AgentTable, MAIN_PM_UUID) is not None:
        return
    session.add(
        AgentTable(
            id=MAIN_PM_UUID,
            name="main-pm",
            slug="main-pm",
            role=AgentRole.MAIN_PM,
            team=Team.MAIN_PM,
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
    await _seed_main_pm(session)
    project = ProjectTable(
        id=uuid4(),
        name="Backend Service",
        slug=f"backend-svc-{uuid4().hex[:6]}",
        git_url=_SEED_GIT_URL,
        assigned_cell=Team.BACKEND,
        created_by=system.id,
        board_programs=["pest_control"],
    )
    session.add(project)
    await session.flush()
    task = TaskTable(
        id=uuid4(),
        title="Pest Control exploration cycle",
        description="Hunt latent defects and propose a bug hunt.",
        acceptance_criteria=["propose_bug_hunt() called once"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=system.id,
        assigned_to=po.id,
        team=Team.BOARD,
        source=PEST_CONTROL_SOURCE,
        confirmed_by_human=False,
    )
    session.add(task)
    await session.flush()
    markers.set_pest_hunt(
        task,
        {
            "items": [
                {
                    "id": "item-0",
                    "title": "Fix stale worktree venv",
                    "description": "Fresh worktrees silently reuse a rotted venv",
                    "acceptance_criteria": ["fresh worktree passes make quality"],
                    "project_slug": project.slug,
                    "team": "backend",
                    "priority": 2,
                    "evidence": "task_review_findings row F-abc123 waived 4x",
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
    app.include_router(pest_control_router, prefix="/api/pest-control")

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
    # The approve/reject routes commit explicitly (write-route convention),
    # so a leftover ``board_programs=["pest_control"]`` opt-in from an
    # approve/reject test here would otherwise outlive this test in the
    # shared, cross-test-persistent DB and poison every later real-DB
    # pest_control unit test that asserts an exact opted-in-project set
    # (e.g. "no project opted in"). Unlike roadmap (org-scoped, no opt-in
    # field to leak) this program is project-scoped, so this cleanup has no
    # roadmap-side counterpart to mirror. Reset, not delete — a materialized
    # BACKLOG task may still hold this project's id as an FK. Unconditional —
    # a no-op for the tests here that never wrote anything.
    await db_session.execute(
        update(ProjectTable)
        .where(ProjectTable.git_url == _SEED_GIT_URL)
        .values(board_programs=None)
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_list_cycles_returns_authored_cycle(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task, _project = await _seed_cycle(db_session)
    resp = await ceo_client.get("/api/pest-control/cycles")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert len(body) == 1
    assert body[0]["task_id"] == str(task.id)
    assert len(body[0]["items"]) == 1
    assert body[0]["items"][0]["evidence"]


@pytest.mark.asyncio
async def test_approve_item_materializes_main_pm_owned_task(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """Defect fix: see test_roadmap_routes.py's identical assertion update."""
    task, _project = await _seed_cycle(db_session)
    resp = await ceo_client.post(
        f"/api/pest-control/cycles/{task.id}/items/item-0/approve"
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "approved"
    assert body["materialized_task_id"] is not None

    materialized = await db_session.get(TaskTable, UUID(body["materialized_task_id"]))
    assert materialized is not None
    assert materialized.status == TaskStatus.PENDING
    assert materialized.assigned_to == MAIN_PM_UUID
    assert materialized.parent_task_id is None


@pytest.mark.asyncio
async def test_approve_unknown_item_is_404(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task, _project = await _seed_cycle(db_session)
    resp = await ceo_client.post(
        f"/api/pest-control/cycles/{task.id}/items/no-such-item/approve"
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_reject_item_records_reason(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task, _project = await _seed_cycle(db_session)
    resp = await ceo_client.post(
        f"/api/pest-control/cycles/{task.id}/items/item-0/reject",
        json={"reason": "already fixed elsewhere"},
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "rejected"

    refreshed = await db_session.get(TaskTable, task.id)
    assert refreshed is not None
    payload = markers.get_pest_hunt(refreshed)
    assert payload is not None
    assert payload["items"][0]["reject_reason"] == "already fixed elsewhere"


@pytest.mark.asyncio
async def test_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    await _seed_cycle(db_session)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        list_resp = await client.get("/api/pest-control/cycles")
    assert list_resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()

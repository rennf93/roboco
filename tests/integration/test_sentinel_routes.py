"""Sentinel (Board Program) engine route coverage — CEO-only, read-only.
Mirrors test_periscope_routes.py minus the market-brief-specific findings
shape: a quality report is a report, not a queue item."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.sentinel import router as sentinel_router
from roboco.db.tables import AgentTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext
from roboco.services.task import SENTINEL_SOURCE
from sqlalchemy import update

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


async def _seed_completed_report(session: AsyncSession) -> TaskTable:
    system = await _seed_agent(session, AgentRole.SYSTEM, "system")
    auditor = await _seed_agent(session, AgentRole.AUDITOR, "auditor")
    await _seed_ceo(session)
    task = TaskTable(
        id=uuid4(),
        title="Sentinel drift-watch cycle",
        description="Assess org-wide quality drift and file ONE report.",
        acceptance_criteria=["propose_quality_report() called once"],
        status=TaskStatus.COMPLETED,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=system.id,
        assigned_to=auditor.id,
        team=Team.BOARD,
        source=SENTINEL_SOURCE,
        confirmed_by_human=False,
    )
    session.add(task)
    await session.flush()
    markers.set_quality_report(
        task,
        {
            "headline": "Waived findings climbed sharply this week",
            "items": [
                {
                    "id": "item-0",
                    "area": "waivers",
                    "observation": "Minor findings keep getting waived in one file",
                    "evidence": "5 waived-minor findings this week (prior: 1)",
                    "suggested_action": "Convert to a Pest Control bug task",
                }
            ],
            "overall_assessment": "Drift is concentrated, not systemic",
        },
    )
    await session.flush()
    return task


def _build_app(db_session: AsyncSession, role: AgentRole, agent_id: UUID) -> FastAPI:
    app = FastAPI()
    app.include_router(sentinel_router, prefix="/api/sentinel")

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
    # This route only reads — nothing here writes/commits — but a sibling
    # real-DB suite could still leave a stray board_sentinel task behind
    # from an earlier failed run; keep the cleanup unconditional and cheap.
    await db_session.execute(
        update(TaskTable)
        .where(TaskTable.source == SENTINEL_SOURCE)
        .values(status=TaskStatus.CANCELLED)
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_list_reports_empty_when_none_filed(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.get("/api/sentinel/reports")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_reports_returns_filed_report(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_completed_report(db_session)
    resp = await ceo_client.get("/api/sentinel/reports")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert len(body) == 1
    row = body[0]
    assert row["task_id"] == str(task.id)
    assert row["headline"] == "Waived findings climbed sharply this week"
    assert len(row["items"]) == 1
    assert row["items"][0]["area"] == "waivers"
    assert row["items"][0]["suggested_action"] == "Convert to a Pest Control bug task"
    assert row["overall_assessment"] == "Drift is concentrated, not systemic"


@pytest.mark.asyncio
async def test_list_reports_omits_non_terminal_exploration(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """A still-open (PENDING) exploration cycle carries no report yet — it
    must never render as an empty/blank report row."""
    system = await _seed_agent(db_session, AgentRole.SYSTEM, "system")
    auditor = await _seed_agent(db_session, AgentRole.AUDITOR, "auditor")
    db_session.add(
        TaskTable(
            id=uuid4(),
            title="Sentinel drift-watch cycle",
            description="x",
            acceptance_criteria=["x"],
            status=TaskStatus.PENDING,
            priority=2,
            task_type=TaskType.ADMINISTRATIVE,
            nature=TaskNature.NON_TECHNICAL,
            estimated_complexity=Complexity.LOW,
            created_by=system.id,
            assigned_to=auditor.id,
            team=Team.BOARD,
            source=SENTINEL_SOURCE,
            confirmed_by_human=False,
        )
    )
    await db_session.flush()
    resp = await ceo_client.get("/api/sentinel/reports")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == []


@pytest.mark.asyncio
async def test_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    await _seed_completed_report(db_session)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/sentinel/reports")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()

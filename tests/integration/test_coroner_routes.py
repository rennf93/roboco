"""Coroner (Board Program) engine route coverage — CEO-only read-only
Postmortems list. Unlike Pest Control/Roadmap there is no approve/reject
action: a postmortem completes atomically at propose_postmortem time.
Mirrors test_pest_control_routes.py's harness shape."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.coroner import router as coroner_router
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext
from roboco.services.task import CORONER_SOURCE

CEO_UUID = _foundation.AGENTS["ceo"].uuid
AUDITOR_UUID = _foundation.AGENTS["auditor"].uuid

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_system_and_auditor(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (_foundation.AGENTS["system"].uuid, "system", AgentRole.SYSTEM, None),
        (AUDITOR_UUID, "auditor", AgentRole.AUDITOR, Team.BOARD),
    ):
        if await session.get(AgentTable, uuid) is not None:
            continue
        session.add(
            AgentTable(
                id=uuid,
                name=slug,
                slug=slug,
                role=role,
                team=team,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="x",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
    await session.flush()


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


async def _seed_completed_postmortem(session: AsyncSession) -> TaskTable:
    await _seed_system_and_auditor(session)
    await _seed_ceo(session)
    project = ProjectTable(
        id=uuid4(),
        name="Backend Service",
        slug=f"backend-svc-{uuid4().hex[:6]}",
        git_url=f"https://github.com/x/{uuid4().hex[:6]}.git",
        assigned_cell=Team.BACKEND,
        created_by=_foundation.AGENTS["system"].uuid,
    )
    session.add(project)
    await session.flush()
    incident_id = uuid4()
    task = TaskTable(
        id=uuid4(),
        title="Coroner postmortem",
        description="Autopsy the chronic task.",
        acceptance_criteria=["propose_postmortem() called once"],
        status=TaskStatus.COMPLETED,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=_foundation.AGENTS["system"].uuid,
        assigned_to=AUDITOR_UUID,
        team=Team.BOARD,
        source=CORONER_SOURCE,
        confirmed_by_human=False,
        project_id=project.id,
    )
    session.add(task)
    await session.flush()
    markers.set_coroner_incident(
        task,
        {
            "incident_task_id": str(incident_id),
            "kind": "bounced",
            "revision_count": 3,
            "title": "Chronic task",
        },
    )
    markers.set_coroner_postmortem(
        task,
        {
            "incident_summary": "the task bounced 3 times over a stale venv",
            "root_cause": "the gate never verified the venv's dev extras",
            "failed_stage": "awaiting_qa",
            "process_change": {
                "kind": "conventions_rule",
                "description": "add a venv-freshness check",
            },
            "playbook_id": None,
        },
    )
    await session.flush()
    return task


def _build_app(db_session: AsyncSession, role: AgentRole, agent_id: UUID) -> FastAPI:
    app = FastAPI()
    app.include_router(coroner_router, prefix="/api/coroner")

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


@pytest_asyncio.fixture
async def dev_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    dev_id = uuid4()
    app = _build_app(db_session, AgentRole.DEVELOPER, dev_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_postmortems_empty(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    await _seed_ceo(db_session)
    resp = await ceo_client.get("/api/coroner/postmortems")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_postmortems_returns_completed_autopsy(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_completed_postmortem(db_session)
    resp = await ceo_client.get("/api/coroner/postmortems")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert len(body) == 1
    row = body[0]
    assert row["task_id"] == str(task.id)
    assert row["incident_kind"] == "bounced"
    assert row["incident_title"] == "Chronic task"
    assert row["root_cause"] == "the gate never verified the venv's dev extras"
    assert row["process_change_kind"] == "conventions_rule"
    assert row["failed_stage"] == "awaiting_qa"


@pytest.mark.asyncio
async def test_list_postmortems_derives_not_applicable_for_playbook_kind(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """A playbook-kind process change reads as not_applicable even when the
    stored marker has NO status key — legacy rows predate the propose-time
    stamp, and the raw default ("proposed") left the panel rendering
    approve/dismiss buttons both verbs refuse forever."""
    task = await _seed_completed_postmortem(db_session)
    payload = markers.get_coroner_postmortem(task)
    assert payload is not None
    payload["process_change"] = {
        "kind": "playbook",
        "description": "run the named command end-to-end",
    }
    markers.set_coroner_postmortem(task, payload)
    await db_session.flush()
    resp = await ceo_client.get("/api/coroner/postmortems")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()[0]["process_change_status"] == "not_applicable"


@pytest.mark.asyncio
async def test_list_postmortems_forbidden_for_non_ceo(
    dev_client: AsyncClient,
) -> None:
    resp = await dev_client.get("/api/coroner/postmortems")
    assert resp.status_code == HTTPStatus.FORBIDDEN

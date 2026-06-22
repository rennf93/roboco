"""Convention-findings persistence + the violations-feed route."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.project import router as project_router
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.permissions import AgentContext
from roboco.services.conventions import get_conventions_service

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}

_FINDINGS = [
    {
        "file": "app/routers/u.py",
        "line": 2,
        "kind": "model",
        "rule": "no_models_in_routers",
        "level": "block",
        "message": "model in router",
        "fix_hint": "move it",
    },
    {
        "file": "app/routers/u.py",
        "line": 9,
        "kind": None,
        "rule": "no_inline_comments",
        "level": "warn",
        "message": "inline comment",
        "fix_hint": "remove",
    },
]


async def _seed_project(db: AsyncSession) -> ProjectTable:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db.add(agent)
    await db.flush()
    project = ProjectTable(
        id=uuid4(),
        name="C-Proj",
        slug=f"c-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db.add(project)
    await db.flush()
    return project


async def test_record_then_recent_findings(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session)
    svc = get_conventions_service(db_session)
    pid = UUID(str(project.id))
    await svc.record_findings(pid, uuid4(), _FINDINGS)
    recent = await svc.recent_findings(pid)
    assert len(recent) == len(_FINDINGS)
    rules = {f["rule"] for f in recent}
    assert rules == {"no_models_in_routers", "no_inline_comments"}
    assert all(f["detected_at"] for f in recent)


async def test_record_replaces_prior_findings_for_task(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    svc = get_conventions_service(db_session)
    pid = UUID(str(project.id))
    task = uuid4()
    await svc.record_findings(pid, task, _FINDINGS)
    await svc.record_findings(pid, task, _FINDINGS[:1])  # latest wins
    recent = await svc.recent_findings(pid)
    assert len(recent) == len(_FINDINGS[:1])
    assert recent[0]["rule"] == "no_models_in_routers"


async def test_record_skips_malformed_entries(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session)
    svc = get_conventions_service(db_session)
    pid = UUID(str(project.id))
    # a could_not_run entry (no file/rule) must not be recorded
    await svc.record_findings(pid, uuid4(), [{"could_not_run": True, "reason": "x"}])
    assert await svc.recent_findings(pid) == []


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    agent = AgentTable(
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
    db_session.add(agent)
    await db_session.flush()
    app = FastAPI()
    app.include_router(project_router, prefix="/api/projects")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", agent.id), role=AgentRole.MAIN_PM, team=None
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_findings_route_returns_recorded(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    project = await _seed_project(db_session)
    pid = UUID(str(project.id))
    await get_conventions_service(db_session).record_findings(pid, uuid4(), _FINDINGS)
    resp = await client.get(
        f"/api/projects/{project.id}/conventions/findings", headers=_HDR
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert len(body) == len(_FINDINGS)
    assert {f["rule"] for f in body} == {"no_models_in_routers", "no_inline_comments"}

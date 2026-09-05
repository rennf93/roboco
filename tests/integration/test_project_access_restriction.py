"""Project agent-access restriction — end-to-end coverage.

Ties together the write routes (POST/DELETE /projects/{id}/access/{agent_id},
roboco/api/routes/project.py:406,449), the services-layer rule
(ProjectService.check_agent_access, roboco/services/project.py:698), and the
Choreographer's structured refusal shape (agent_access_denied_guard,
roboco/services/gateway/claim_guards.py) -- the enforcement chokepoint wired
into i_will_work_on / i_will_plan claims
(roboco/services/gateway/choreographer/_impl.py:_agent_access_claim_guard).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import cast as typing_cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.project import router as project_router
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.permissions import AgentContext
from roboco.services.gateway.claim_guards import agent_access_denied_guard
from roboco.services.project import ProjectService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


@pytest_asyncio.fixture
async def access_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Real FastAPI app + real db_session against the project router,
    mirroring tests/integration/test_project_routes.py's project_client."""
    pm_agent = AgentTable(
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
    db_session.add(pm_agent)
    await db_session.flush()

    app = FastAPI()
    app.include_router(project_router, prefix="/api/projects")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=typing_cast("UUID", pm_agent.id),
            role=AgentRole.MAIN_PM,
            team=None,
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def _project_payload() -> dict:
    return {
        "name": f"AccessProj {uuid4().hex[:6]}",
        "slug": f"access-proj-{uuid4().hex[:6]}",
        "git_url": "https://github.com/example/access.git",
        "default_branch": "main",
        "assigned_cell": "backend",
    }


async def _make_agent(
    db_session: AsyncSession, *, team: Team = Team.BACKEND
) -> AgentTable:
    agent = AgentTable(
        id=uuid4(),
        name=f"Agent {uuid4().hex[:6]}",
        slug=f"agent-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=team,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


def _aid(agent: AgentTable) -> UUID:
    """AgentTable.id reads as the SQLAlchemy column descriptor to mypy at
    this call shape; cast to the real runtime UUID (mirrors the existing
    ``cast("UUID", agent.id)`` pattern in test_project_routes.py)."""
    return typing_cast("UUID", agent.id)


@pytest.mark.asyncio
async def test_restrict_refuses_non_allowed_same_cell_agent_with_structured_reason(
    access_client: AsyncClient, db_session: AsyncSession
) -> None:
    """(1) POST /access/{agent_id} restricts the project; a same-cell agent
    NOT on the list is refused at the enforcement chokepoint with a
    structured not_authorized reason naming the project + agent."""
    create = await access_client.post(
        "/api/projects", json=_project_payload(), headers=_HDR
    )
    assert create.status_code == HTTPStatus.CREATED
    project_id = UUID(create.json()["id"])

    allowed_agent = await _make_agent(db_session)
    refused_agent = await _make_agent(db_session)

    restrict = await access_client.post(
        f"/api/projects/{project_id}/access/{allowed_agent.id}", headers=_HDR
    )
    assert restrict.status_code == HTTPStatus.OK

    svc = ProjectService(db_session)
    has_access = await svc.check_agent_access(
        project_id, _aid(refused_agent), Team.BACKEND
    )
    assert has_access is False

    task = type("Task", (), {"id": uuid4()})()
    env = agent_access_denied_guard(task, project_id, _aid(refused_agent), has_access)
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert str(project_id) in body["message"]
    assert str(_aid(refused_agent)) in body["message"]
    assert f"/projects/{project_id}/access/{_aid(refused_agent)}" in body["remediate"]

    # The allowed agent itself keeps access.
    assert (
        await svc.check_agent_access(project_id, _aid(allowed_agent), Team.BACKEND)
        is True
    )


@pytest.mark.asyncio
async def test_removing_restriction_restores_cell_default_access(
    access_client: AsyncClient, db_session: AsyncSession
) -> None:
    """(2) DELETE /access/{agent_id} removing the only allowed agent lifts
    the restriction entirely; the previously-refused agent now passes."""
    create = await access_client.post(
        "/api/projects", json=_project_payload(), headers=_HDR
    )
    project_id = UUID(create.json()["id"])

    allowed_agent = await _make_agent(db_session)
    refused_agent = await _make_agent(db_session)

    await access_client.post(
        f"/api/projects/{project_id}/access/{allowed_agent.id}", headers=_HDR
    )
    svc = ProjectService(db_session)
    assert (
        await svc.check_agent_access(project_id, _aid(refused_agent), Team.BACKEND)
        is False
    )

    remove = await access_client.delete(
        f"/api/projects/{project_id}/access/{allowed_agent.id}", headers=_HDR
    )
    assert remove.status_code == HTTPStatus.OK

    assert (
        await svc.check_agent_access(project_id, _aid(refused_agent), Team.BACKEND)
        is True
    )
    # The formerly-allowed agent also keeps access -- it's cell-default now,
    # not a list that happens to still exclude everyone.
    assert (
        await svc.check_agent_access(project_id, _aid(allowed_agent), Team.BACKEND)
        is True
    )


@pytest.mark.asyncio
async def test_no_restriction_never_refuses_same_cell_agent(
    access_client: AsyncClient, db_session: AsyncSession
) -> None:
    """(4) allowed_agents=None (never restricted) keeps whole-cell access --
    check_agent_access never refuses a same-cell agent, and the guard stays
    inert (no refusal envelope)."""
    create = await access_client.post(
        "/api/projects", json=_project_payload(), headers=_HDR
    )
    project_id = UUID(create.json()["id"])
    agent = await _make_agent(db_session)

    svc = ProjectService(db_session)
    has_access = await svc.check_agent_access(project_id, _aid(agent), Team.BACKEND)
    assert has_access is True

    task = type("Task", (), {"id": uuid4()})()
    env = agent_access_denied_guard(task, project_id, _aid(agent), has_access)
    assert env is None

"""Release-manager route coverage — CEO-only GET / approve / reject."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.release import router as release_router
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext
from roboco.services.release_executor import ReleaseResult
from roboco.services.release_readiness import ReleaseReadinessReport, report_to_dict
from roboco.services.task import RELEASE_MANAGER_SOURCE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_VERSION = "0.13.0"


def _report() -> ReleaseReadinessReport:
    return ReleaseReadinessReport(
        proposed_version=_VERSION,
        bump_kind="minor",
        change_summary=["feat: a thing"],
        drafted_changelog=f"## [{_VERSION}] - 2026-06-25\n\n### Added\n- a thing\n",
        version_bump_plan=["pyproject.toml"],
        gaps=[],
        migration_notes=[],
        gate_state="green",
    )


async def _seed_agent(
    session: AsyncSession, role: AgentRole, slug: str
) -> AgentTable:
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


async def _seed_proposal(session: AsyncSession) -> TaskTable:
    system = await _seed_agent(session, AgentRole.SYSTEM, "system")
    secretary = await _seed_agent(session, AgentRole.SECRETARY, "secretary")
    project = ProjectTable(
        id=uuid4(),
        name="RoboCo",
        slug=f"roboco-{uuid4().hex[:6]}",
        git_url="https://example.com/roboco.git",
        assigned_cell=Team.BACKEND,
        created_by=system.id,
    )
    session.add(project)
    await session.flush()
    task = TaskTable(
        id=uuid4(),
        title=f"Release proposal: v{_VERSION}",
        description="proposal body",
        acceptance_criteria=["CEO approves"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        project_id=project.id,
        created_by=system.id,
        assigned_to=secretary.id,
        team=Team.MAIN_PM,
        source=RELEASE_MANAGER_SOURCE,
        confirmed_by_human=False,
        orchestration_markers={"release_report": report_to_dict(_report())},
    )
    session.add(task)
    await session.flush()
    return task


def _build_app(
    db_session: AsyncSession, role: AgentRole, agent_id: UUID
) -> FastAPI:
    app = FastAPI()
    app.include_router(release_router, prefix="/api/release")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=agent_id, role=role, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    return app


@pytest_asyncio.fixture
async def ceo_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = _build_app(db_session, AgentRole.CEO, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_proposal_returns_open_proposal(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    await _seed_proposal(db_session)
    resp = await ceo_client.get("/api/release/proposal")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["report"]["proposed_version"] == _VERSION
    assert body["report"]["bump_kind"] == "minor"


@pytest.mark.asyncio
async def test_get_proposal_404_when_none(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.get("/api/release/proposal")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_approve_runs_executor_and_completes(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_proposal(db_session)
    published = ReleaseResult(
        status="published",
        version=_VERSION,
        files_changed=["pyproject.toml"],
        commit_sha="abc123",
        release_url=f"https://github.com/x/roboco/releases/tag/v{_VERSION}",
        detail="ok",
    )
    fake_executor = AsyncMock()
    fake_executor.execute = AsyncMock(return_value=published)
    with patch(
        "roboco.services.release_proposal.get_release_executor",
        AsyncMock(return_value=fake_executor),
    ):
        resp = await ceo_client.post("/api/release/proposal/approve")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["status"] == "published"
    fake_executor.execute.assert_awaited_once()
    refreshed = await db_session.get(TaskTable, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_approve_gate_failure_keeps_proposal_open(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_proposal(db_session)
    failed = ReleaseResult(
        status="gate_failed",
        version=_VERSION,
        files_changed=["pyproject.toml"],
        commit_sha=None,
        release_url=None,
        detail="make quality failed",
    )
    fake_executor = AsyncMock()
    fake_executor.execute = AsyncMock(return_value=failed)
    with patch(
        "roboco.services.release_proposal.get_release_executor",
        AsyncMock(return_value=fake_executor),
    ):
        resp = await ceo_client.post("/api/release/proposal/approve")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["status"] == "gate_failed"
    refreshed = await db_session.get(TaskTable, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PENDING  # still held


@pytest.mark.asyncio
async def test_reject_records_changes_and_keeps_open(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_proposal(db_session)
    resp = await ceo_client.post(
        "/api/release/proposal/reject",
        json={"required_changes": "Tighten the CHANGELOG wording for the API change."},
    )
    assert resp.status_code == HTTPStatus.OK
    assert "Tighten the CHANGELOG" in (resp.json()["required_changes"] or "")
    refreshed = await db_session.get(TaskTable, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PENDING  # stays held for revision


@pytest.mark.asyncio
async def test_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    await _seed_proposal(db_session)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        get_resp = await client.get("/api/release/proposal")
        approve_resp = await client.post("/api/release/proposal/approve")
        reject_resp = await client.post(
            "/api/release/proposal/reject", json={"required_changes": "x" * 20}
        )
    assert get_resp.status_code == HTTPStatus.FORBIDDEN
    assert approve_resp.status_code == HTTPStatus.FORBIDDEN
    assert reject_resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()

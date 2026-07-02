"""PM-lighter PATCH surface on PATCH /api/tasks/{id}.

The CEO's spec: PMs get a *lighter* content-only slice of the same REST PATCH
path the Secretary's edit directive uses — title/description/
acceptance_criteria/priority — scoped to tasks in the PM's remit (cell_pm:
own team only; main_pm: any team). No status changes, no structural/
ownership fields, no git fields — those stay on the lifecycle-verb surface.

cell_pm/main_pm already hold TaskAction.ASSIGN (see TASK_PERMISSIONS), which
is *not* team-scoped in ``can_perform_task_action`` — so absent this gate a
PM would already ride the CEO/Board/Auditor "full admin" bypass on this
route (any field, any team). These tests pin the narrower behavior down.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
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


async def _make_client(
    db_session: AsyncSession, *, role: AgentRole, team: Team | None
) -> dict[str, Any]:
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"pm-{uuid4().hex[:8]}",
        role=role,
        team=team,
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
        name="PL-Proj",
        slug=f"pl-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/pl.git",
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
        return AgentContext(agent_id=cast("UUID", pm.id), role=role, team=team)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return {
        "client": client,
        "app": app,
        "agent": pm,
        "project": project,
        "db": db_session,
    }


@pytest_asyncio.fixture
async def cell_pm_client(db_session: AsyncSession) -> AsyncIterator[dict]:
    """A backend cell PM (ASSIGN, no UPDATE_OWN)."""
    setup = await _make_client(db_session, role=AgentRole.CELL_PM, team=Team.BACKEND)
    async with setup["client"]:
        yield setup
    setup["app"].dependency_overrides.clear()


@pytest_asyncio.fixture
async def main_pm_client(db_session: AsyncSession) -> AsyncIterator[dict]:
    """The Main PM (ASSIGN, no team restriction)."""
    setup = await _make_client(db_session, role=AgentRole.MAIN_PM, team=None)
    async with setup["client"]:
        yield setup
    setup["app"].dependency_overrides.clear()


def _seed_task(setup: dict, **kw: Any) -> TaskTable:
    team = kw.pop("team", Team.BACKEND)
    task = TaskTable(
        id=uuid4(),
        title=kw.pop("title", "t"),
        description=kw.pop("description", "d"),
        acceptance_criteria=["ac"],
        status=kw.pop("status", TaskStatus.IN_PROGRESS),
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=setup["project"].id,
        created_by=setup["agent"].id,
        assigned_to=None,
        team=team,
    )
    setup["db"].add(task)
    return task


def _hdr(agent: AgentTable, role: AgentRole) -> dict[str, str]:
    return {"X-Agent-ID": str(agent.id), "X-Agent-Role": role.value}


@pytest.mark.asyncio
async def test_cell_pm_can_patch_content_field_on_own_team_task(
    cell_pm_client: dict,
) -> None:
    setup = cell_pm_client
    task = _seed_task(setup, team=Team.BACKEND)
    await setup["db"].flush()
    response = await setup["client"].patch(
        f"/api/tasks/{task.id}",
        json={"title": "Sharper title from the cell PM"},
        headers=_hdr(setup["agent"], AgentRole.CELL_PM),
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["title"] == "Sharper title from the cell PM"


@pytest.mark.asyncio
async def test_cell_pm_cannot_patch_task_outside_own_team(
    cell_pm_client: dict,
) -> None:
    """ASSIGN is not team-scoped — without an explicit check a cell PM would
    ride the same admin bypass CEO/Board get on ANY team's task."""
    setup = cell_pm_client
    task = _seed_task(setup, team=Team.FRONTEND)
    await setup["db"].flush()
    response = await setup["client"].patch(
        f"/api/tasks/{task.id}",
        json={"title": "Should not land"},
        headers=_hdr(setup["agent"], AgentRole.CELL_PM),
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize(
    "field,value",
    [
        ("dev_notes", "trying to sneak a note in"),
        ("team", "frontend"),
        ("assigned_to", str(uuid4())),
    ],
)
@pytest.mark.asyncio
async def test_cell_pm_cannot_patch_fields_outside_lighter_allowlist(
    cell_pm_client: dict, field: str, value: object
) -> None:
    setup = cell_pm_client
    task = _seed_task(setup, team=Team.BACKEND)
    await setup["db"].flush()
    response = await setup["client"].patch(
        f"/api/tasks/{task.id}",
        json={field: value},
        headers=_hdr(setup["agent"], AgentRole.CELL_PM),
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_cell_pm_cannot_change_status_via_patch(cell_pm_client: dict) -> None:
    """No status changes beyond what the lifecycle verbs already grant — the
    PM-lighter PATCH surface must not become a side-door status override."""
    setup = cell_pm_client
    task = _seed_task(setup, team=Team.BACKEND, status=TaskStatus.IN_PROGRESS)
    await setup["db"].flush()
    response = await setup["client"].patch(
        f"/api/tasks/{task.id}",
        json={"status": "completed", "force": True},
        headers=_hdr(setup["agent"], AgentRole.CELL_PM),
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_main_pm_can_patch_content_field_on_any_team_task(
    main_pm_client: dict,
) -> None:
    setup = main_pm_client
    task = _seed_task(setup, team=Team.UX_UI)
    await setup["db"].flush()
    response = await setup["client"].patch(
        f"/api/tasks/{task.id}",
        json={"description": "A clarified description of at least twenty chars."},
        headers=_hdr(setup["agent"], AgentRole.MAIN_PM),
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_main_pm_cannot_patch_privileged_field(main_pm_client: dict) -> None:
    setup = main_pm_client
    task = _seed_task(setup, team=Team.BACKEND)
    await setup["db"].flush()
    response = await setup["client"].patch(
        f"/api/tasks/{task.id}",
        json={"parent_task_id": str(uuid4())},
        headers=_hdr(setup["agent"], AgentRole.MAIN_PM),
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_main_pm_cannot_change_status_via_patch(main_pm_client: dict) -> None:
    setup = main_pm_client
    task = _seed_task(setup, status=TaskStatus.AWAITING_PM_REVIEW)
    await setup["db"].flush()
    response = await setup["client"].patch(
        f"/api/tasks/{task.id}",
        json={"status": "completed"},
        headers=_hdr(setup["agent"], AgentRole.MAIN_PM),
    )
    assert response.status_code == HTTPStatus.FORBIDDEN

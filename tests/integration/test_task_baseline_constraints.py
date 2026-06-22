"""TaskService.create appends the project's baseline constraints (flag-gated)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from roboco.config import settings
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Complexity, Team
from roboco.models.base import TaskNature
from roboco.models.task import TaskCreateRequest, TaskType
from roboco.services.task import TaskService

if TYPE_CHECKING:
    import pytest
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed(db: AsyncSession) -> tuple[AgentTable, ProjectTable]:
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
    return agent, project


def _req(
    agent: AgentTable, project: ProjectTable, description: str
) -> TaskCreateRequest:
    return TaskCreateRequest(
        title="A task",
        description=description,
        acceptance_criteria=["it works"],
        team=Team.BACKEND,
        created_by=UUID(str(agent.id)),
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        project_id=UUID(str(project.id)),
    )


async def test_baseline_attached_when_flag_on(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    agent, project = await _seed(db_session)
    task = await TaskService(db_session).create(_req(agent, project, "Do the work"))
    assert task.description is not None
    assert "## Constraints" in task.description
    assert "no models in routers" in task.description


async def test_flag_off_attaches_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", False)
    agent, project = await _seed(db_session)
    task = await TaskService(db_session).create(_req(agent, project, "Do the work"))
    assert task.description == "Do the work"


async def test_attach_is_idempotent_when_section_present(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    agent, project = await _seed(db_session)
    seeded = "Do the work\n\n## Constraints\n- already here"
    task = await TaskService(db_session).create(_req(agent, project, seeded))
    assert task.description is not None
    assert task.description.count("## Constraints") == 1

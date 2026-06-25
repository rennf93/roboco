"""Project update accepts the autonomous-maintenance opt-in fields.

The CI-watch + dep-update per-project columns must be settable through the
normal ProjectUpdate path (what the panel edit-project dialog calls), or the
operator can't opt a project in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.project import ProjectUpdate
from roboco.services.project import get_project_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_project(db_session: AsyncSession) -> ProjectTable:
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
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    return project


@pytest.mark.asyncio
async def test_update_sets_autonomy_opt_ins(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session)
    svc = get_project_service(db_session)

    await svc.update(
        project.id,
        ProjectUpdate(
            ci_watch_enabled=True,
            ci_watch_workflow="ci.yml",
            dep_update_command="uv lock --upgrade",
            dep_update_paths=["uv.lock"],
        ),
    )

    reloaded = await svc.get(project.id)
    assert reloaded is not None
    assert reloaded.ci_watch_enabled is True
    assert reloaded.ci_watch_workflow == "ci.yml"
    assert reloaded.dep_update_command == "uv lock --upgrade"
    assert reloaded.dep_update_paths == ["uv.lock"]

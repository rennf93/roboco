"""The project_conventions_cache table round-trips JSONB and enforces uniqueness."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.db.tables import (
    AgentTable,
    ProjectConventionsCacheTable,
    ProjectTable,
)
from roboco.models import AgentRole, AgentStatus, Team
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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


async def test_cache_row_round_trips_jsonb(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session)
    row = ProjectConventionsCacheTable(
        id=uuid4(),
        project_id=project.id,
        commit_sha="abc1234",
        effective_map={
            "version": 1,
            "rules": {
                "no_models_in_routers": {
                    "name": "no_models_in_routers",
                    "level": "block",
                }
            },
        },
        status="ok",
    )
    db_session.add(row)
    await db_session.flush()
    await db_session.refresh(row)

    fetched = (
        await db_session.execute(
            select(ProjectConventionsCacheTable).where(
                ProjectConventionsCacheTable.project_id == project.id
            )
        )
    ).scalar_one()
    assert fetched.status == "ok"
    assert fetched.effective_map["rules"]["no_models_in_routers"]["level"] == "block"
    assert fetched.derived_at is not None


async def test_project_sha_uniqueness_is_enforced(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session)
    db_session.add(
        ProjectConventionsCacheTable(
            id=uuid4(),
            project_id=project.id,
            commit_sha="dup",
            effective_map={},
            status="ok",
        )
    )
    await db_session.flush()
    db_session.add(
        ProjectConventionsCacheTable(
            id=uuid4(),
            project_id=project.id,
            commit_sha="dup",
            effective_map={},
            status="ok",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()

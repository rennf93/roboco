"""Per-project sandbox extensions opt-in column (migration 072).

Migration 072 adds ``projects.sandbox_extensions`` (jsonb null). The real
upgrade/downgrade chain is verified separately against a throwaway Postgres;
these assertions guard the resulting schema shape and a value round-trip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from sqlalchemy import select

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
        name="B-Proj",
        slug=f"b-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    return project


@pytest.mark.asyncio
async def test_sandbox_extensions_column_default_null(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    assert project.sandbox_extensions is None


@pytest.mark.asyncio
async def test_sandbox_extensions_column_round_trip(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    project.sandbox_extensions = {
        "postgres": ["vector", "postgis"],
        "redis": ["search"],
    }
    await db_session.flush()
    row = (
        await db_session.execute(
            select(ProjectTable).where(ProjectTable.id == project.id)
        )
    ).scalar_one()
    assert row.sandbox_extensions == {
        "postgres": ["vector", "postgis"],
        "redis": ["search"],
    }

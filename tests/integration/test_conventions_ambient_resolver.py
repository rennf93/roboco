"""conventions_ambient_layer: flag-gated, project-aware ambient-block resolver."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from roboco.agents.factories._base import conventions_ambient_layer
from roboco.config import settings
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team

if TYPE_CHECKING:
    import pytest
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


async def test_ambient_block_when_flag_on(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    project = await _seed_project(db_session)
    block = await conventions_ambient_layer(db_session, project)
    assert block is not None
    assert block.startswith("## Architectural Standard")


async def test_none_when_flag_off(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", False)
    project = await _seed_project(db_session)
    assert await conventions_ambient_layer(db_session, project) is None


async def test_none_when_no_project(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    assert await conventions_ambient_layer(db_session, None) is None

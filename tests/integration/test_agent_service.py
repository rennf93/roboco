"""AgentService coverage — list/get by uuid/slug + raise."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.services.agent import AgentService
from roboco.services.base import NotFoundError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def agent_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    dev = AgentTable(
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
    db_session.add(dev)
    await db_session.flush()
    yield {"svc": AgentService(db_session), "agent": dev}


@pytest.mark.asyncio
async def test_list_agents_no_filter(agent_setup: dict) -> None:
    rows = await agent_setup["svc"].list_agents()
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_list_agents_filter_by_slug(agent_setup: dict) -> None:
    svc = agent_setup["svc"]
    rows = await svc.list_agents(slug=agent_setup["agent"].slug)
    assert len(rows) == 1
    assert rows[0].id == agent_setup["agent"].id


@pytest.mark.asyncio
async def test_list_agents_filter_by_role(agent_setup: dict) -> None:
    svc = agent_setup["svc"]
    rows = await svc.list_agents(role=AgentRole.DEVELOPER)
    assert all(r.role == AgentRole.DEVELOPER for r in rows)


@pytest.mark.asyncio
async def test_list_agents_filter_by_team(agent_setup: dict) -> None:
    svc = agent_setup["svc"]
    rows = await svc.list_agents(team=Team.BACKEND)
    assert all(r.team == Team.BACKEND for r in rows)


@pytest.mark.asyncio
async def test_get_by_uuid(agent_setup: dict) -> None:
    svc = agent_setup["svc"]
    fetched = await svc.get_by_uuid(agent_setup["agent"].id)
    assert fetched is not None
    assert fetched.id == agent_setup["agent"].id


@pytest.mark.asyncio
async def test_get_by_uuid_returns_none(agent_setup: dict) -> None:
    svc = agent_setup["svc"]
    assert await svc.get_by_uuid(uuid4()) is None


@pytest.mark.asyncio
async def test_get_by_slug(agent_setup: dict) -> None:
    svc = agent_setup["svc"]
    fetched = await svc.get_by_slug(agent_setup["agent"].slug)
    assert fetched is not None


@pytest.mark.asyncio
async def test_get_by_slug_returns_none(agent_setup: dict) -> None:
    svc = agent_setup["svc"]
    assert await svc.get_by_slug("ghost-agent") is None


@pytest.mark.asyncio
async def test_get_by_uuid_or_slug_with_uuid(agent_setup: dict) -> None:
    svc = agent_setup["svc"]
    fetched = await svc.get_by_uuid_or_slug_or_raise(str(agent_setup["agent"].id))
    assert fetched.id == agent_setup["agent"].id


@pytest.mark.asyncio
async def test_get_by_uuid_or_slug_with_slug(agent_setup: dict) -> None:
    svc = agent_setup["svc"]
    fetched = await svc.get_by_uuid_or_slug_or_raise(agent_setup["agent"].slug)
    assert fetched.id == agent_setup["agent"].id


@pytest.mark.asyncio
async def test_get_by_uuid_or_slug_raises(agent_setup: dict) -> None:
    svc = agent_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.get_by_uuid_or_slug_or_raise("ghost-agent")

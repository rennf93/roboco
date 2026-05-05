"""BaseRepository coverage — concrete subclass over AgentTable."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.services.base import NotFoundError
from roboco.services.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


class _AgentRepo(BaseRepository[AgentTable]):
    model = AgentTable
    model_name = "Agent"


@pytest_asyncio.fixture
async def repo_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    repo = _AgentRepo(db_session)
    a = AgentTable(
        id=uuid4(),
        name="Dev1",
        slug=f"dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    b = AgentTable(
        id=uuid4(),
        name="Dev2",
        slug=f"dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.FRONTEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add_all([a, b])
    await db_session.flush()
    yield {"repo": repo, "a": a, "b": b}


@pytest.mark.asyncio
async def test_get_returns_entity(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    found = await repo.get(repo_setup["a"].id)
    assert found is not None
    assert found.id == repo_setup["a"].id


@pytest.mark.asyncio
async def test_get_returns_none_for_missing(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    assert await repo.get(uuid4()) is None


@pytest.mark.asyncio
async def test_get_or_raise_raises(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    with pytest.raises(NotFoundError):
        await repo.get_or_raise(uuid4())


@pytest.mark.asyncio
async def test_get_or_raise_returns_entity(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    fetched = await repo.get_or_raise(repo_setup["a"].id)
    assert fetched.id == repo_setup["a"].id


@pytest.mark.asyncio
async def test_get_all_with_default_ordering(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    rows = await repo.get_all(limit=100)
    ids = {r.id for r in rows}
    assert repo_setup["a"].id in ids
    assert repo_setup["b"].id in ids


@pytest.mark.asyncio
async def test_get_all_with_explicit_order(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    rows = await repo.get_all(limit=100, order_by=AgentTable.name.asc())
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_find_by(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    rows = await repo.find_by(AgentTable.team == Team.BACKEND)
    assert any(r.id == repo_setup["a"].id for r in rows)


@pytest.mark.asyncio
async def test_find_by_with_explicit_order(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    rows = await repo.find_by(
        AgentTable.team == Team.BACKEND, order_by=AgentTable.name.asc()
    )
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_find_one_returns_entity(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    row = await repo.find_one(AgentTable.id == repo_setup["a"].id)
    assert row is not None
    assert row.id == repo_setup["a"].id


@pytest.mark.asyncio
async def test_find_one_returns_none(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    assert await repo.find_one(AgentTable.id == uuid4()) is None


@pytest.mark.asyncio
async def test_exists_true(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    assert await repo.exists(repo_setup["a"].id) is True


@pytest.mark.asyncio
async def test_exists_false(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    assert await repo.exists(uuid4()) is False


@pytest.mark.asyncio
async def test_count_with_conditions(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    count = await repo.count(AgentTable.team == Team.BACKEND)
    assert count >= 1


@pytest.mark.asyncio
async def test_count_without_conditions(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    count = await repo.count()
    assert count >= 2


@pytest.mark.asyncio
async def test_add(repo_setup: dict, db_session: AsyncSession) -> None:
    repo = repo_setup["repo"]
    new_agent = AgentTable(
        id=uuid4(),
        name="New",
        slug=f"new-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    added = await repo.add(new_agent)
    assert added.id == new_agent.id


@pytest.mark.asyncio
async def test_delete(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    await repo.delete(repo_setup["a"])
    assert await repo.get(repo_setup["a"].id) is None


@pytest.mark.asyncio
async def test_delete_by_id_returns_true(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    deleted = await repo.delete_by_id(repo_setup["a"].id)
    assert deleted is True


@pytest.mark.asyncio
async def test_delete_by_id_returns_false(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    assert await repo.delete_by_id(uuid4()) is False


@pytest.mark.asyncio
async def test_query_returns_select(repo_setup: dict) -> None:
    repo = repo_setup["repo"]
    query = repo.query()
    rows = await repo.execute_query(query)
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_execute_scalar(repo_setup: dict) -> None:
    from sqlalchemy import func, select

    repo = repo_setup["repo"]
    query = select(func.count(AgentTable.id))
    result = await repo.execute_scalar(query)
    assert isinstance(result, int)

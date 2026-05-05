"""query_helpers coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.services.repositories.query_helpers import (
    agent_id_filter,
    days_ago,
    get_agent_by_slug,
    get_agent_slug,
    pagination,
    resolve_agent_identity,
    resolve_agent_uuid,
    status_filter,
    team_filter,
    timestamp_filter,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Pure query-builder helpers
# ---------------------------------------------------------------------------


def test_days_ago_returns_past_datetime() -> None:
    out = days_ago(7)
    assert out < datetime.now(UTC)
    assert (datetime.now(UTC) - out) >= timedelta(days=6)


def test_pagination_applies_limit_offset() -> None:
    from sqlalchemy import select

    q = select(AgentTable)
    out = pagination(q, limit=10, offset=20)
    # SQLAlchemy compiles the query; we just verify it doesn't crash.
    assert out is not None


def test_status_filter_passes_through_when_none() -> None:
    from sqlalchemy import select

    q = select(AgentTable)
    assert status_filter(q, AgentTable, None) is q


def test_status_filter_applies_when_provided() -> None:
    from sqlalchemy import select

    q = select(AgentTable)
    out = status_filter(q, AgentTable, AgentStatus.ACTIVE)
    assert out is not q  # Different query.


def test_team_filter_passes_through_when_none() -> None:
    from sqlalchemy import select

    q = select(AgentTable)
    assert team_filter(q, AgentTable, None) is q


def test_team_filter_applies_when_provided() -> None:
    from sqlalchemy import select

    q = select(AgentTable)
    out = team_filter(q, AgentTable, Team.BACKEND)
    assert out is not q


def test_agent_id_filter_passes_through_when_none() -> None:
    from sqlalchemy import select

    q = select(AgentTable)
    assert agent_id_filter(q, AgentTable, None) is q


def test_agent_id_filter_applies_when_provided() -> None:
    from sqlalchemy import select

    q = select(AgentTable)
    out = agent_id_filter(q, AgentTable, uuid4(), field_name="id")
    assert out is not q


def test_timestamp_filter_with_since_and_until() -> None:
    from sqlalchemy import select

    q = select(AgentTable)
    out = timestamp_filter(
        q,
        AgentTable,
        since=days_ago(7),
        until=datetime.now(UTC),
    )
    assert out is not q


def test_timestamp_filter_no_args_unchanged() -> None:
    from sqlalchemy import select

    q = select(AgentTable)
    out = timestamp_filter(q, AgentTable)
    # No filter applied.
    assert out is q


# ---------------------------------------------------------------------------
# Async DB resolvers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_agent_uuid_with_uuid_string(
    db_session: AsyncSession,
) -> None:
    aid = uuid4()
    resolved = await resolve_agent_uuid(db_session, str(aid))
    assert resolved == aid


@pytest.mark.asyncio
async def test_resolve_agent_uuid_with_slug(
    db_session: AsyncSession,
) -> None:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"q-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    resolved = await resolve_agent_uuid(db_session, agent.slug)
    assert resolved == agent.id


@pytest.mark.asyncio
async def test_resolve_agent_uuid_with_unknown_slug(
    db_session: AsyncSession,
) -> None:
    assert await resolve_agent_uuid(db_session, "ghost-slug") is None


@pytest.mark.asyncio
async def test_resolve_agent_identity_with_slug(
    db_session: AsyncSession,
) -> None:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"q-id-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    out = await resolve_agent_identity(db_session, agent.slug)
    assert out is not None
    assert out[0] == agent.id


@pytest.mark.asyncio
async def test_resolve_agent_identity_unknown(
    db_session: AsyncSession,
) -> None:
    assert await resolve_agent_identity(db_session, "ghost") is None


@pytest.mark.asyncio
async def test_resolve_agent_identity_unknown_uuid(
    db_session: AsyncSession,
) -> None:
    assert await resolve_agent_identity(db_session, str(uuid4())) is None


@pytest.mark.asyncio
async def test_get_agent_slug_known(db_session: AsyncSession) -> None:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"slug-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    slug = await get_agent_slug(db_session, agent.id)
    assert slug == agent.slug


@pytest.mark.asyncio
async def test_get_agent_slug_missing(db_session: AsyncSession) -> None:
    assert await get_agent_slug(db_session, uuid4()) is None


@pytest.mark.asyncio
async def test_get_agent_by_slug(db_session: AsyncSession) -> None:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"by-slug-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    fetched = await get_agent_by_slug(db_session, agent.slug)
    assert fetched is not None
    assert fetched.id == agent.id


@pytest.mark.asyncio
async def test_get_agent_by_slug_missing(db_session: AsyncSession) -> None:
    assert await get_agent_by_slug(db_session, "ghost-slug") is None

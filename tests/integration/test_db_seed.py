"""DB seed coverage — agent bootstrap."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from roboco.db.seed import bootstrap_database, create_agents

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_create_agents_seeds_defaults(db_session: AsyncSession) -> None:
    agent_ids = await create_agents(db_session)
    assert len(agent_ids) > 0
    # Agents include be-dev-1, be-qa, etc.
    assert any("be-" in slug or "fe-" in slug for slug in agent_ids)


@pytest.mark.asyncio
async def test_create_agents_idempotent(db_session: AsyncSession) -> None:
    first = await create_agents(db_session)
    second = await create_agents(db_session)
    for slug, aid in first.items():
        assert second[slug] == aid


@pytest.mark.asyncio
async def test_bootstrap_database_invokes_full_pipeline() -> None:
    """bootstrap_database wires up init_db + agent seeding."""
    fake_session = AsyncMock()
    fake_session.commit = AsyncMock()

    @asynccontextmanager
    async def _ctx() -> AsyncGenerator[Any]:
        yield fake_session

    with (
        patch("roboco.db.seed.init_db", AsyncMock()) as mock_init,
        patch("roboco.db.seed.get_db_context", _ctx),
        patch(
            "roboco.db.seed.create_agents",
            AsyncMock(return_value={"be-dev-1": "id"}),
        ) as mock_ag,
    ):
        await bootstrap_database()
    mock_init.assert_awaited_once()
    mock_ag.assert_awaited_once()
    fake_session.commit.assert_awaited_once()

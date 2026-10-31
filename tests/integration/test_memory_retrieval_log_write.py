"""The briefing's memory-retrieval log records the agent by slug.

``memory_retrieval_log.agent_slug`` is a slug column (String(64)); the
choreographer wrote ``str(agent_id)``, the agent's UUID, into it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import MemoryRetrievalLogTable
from roboco.foundation.identity import AGENTS
from roboco.services.gateway.choreographer import Choreographer
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


async def _log(db_session: AsyncSession, agent_id: UUID) -> list[str | None]:
    @asynccontextmanager
    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    task = MagicMock(id=uuid4())
    items = [{"source": "roboco://learnings/lrn-abc123"}]
    with patch("roboco.db.base.get_session_factory", return_value=_session):
        await Choreographer._log_memory_retrievals(MagicMock(), agent_id, task, items)
    rows = await db_session.execute(select(MemoryRetrievalLogTable.agent_slug))
    return list(rows.scalars())


@pytest.mark.asyncio
async def test_a_seeded_agent_is_logged_by_its_slug(db_session: AsyncSession) -> None:
    agent = AGENTS["be-dev-1"]
    assert await _log(db_session, agent.uuid) == ["be-dev-1"]


@pytest.mark.asyncio
async def test_an_unknown_agent_is_logged_without_a_slug(
    db_session: AsyncSession,
) -> None:
    assert await _log(db_session, uuid4()) == [None]

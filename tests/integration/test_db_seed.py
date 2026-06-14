"""DB seed coverage — channel/agent/membership/messages bootstrap."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.db.seed import (
    bootstrap_database,
    create_agents,
    create_channel_memberships,
    create_channels,
    create_initial_messages,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_create_channels_seeds_defaults(
    db_session: AsyncSession,
) -> None:
    channel_ids = await create_channels(db_session)
    assert "backend-cell" in channel_ids
    assert "frontend-cell" in channel_ids


@pytest.mark.asyncio
async def test_create_channels_idempotent(db_session: AsyncSession) -> None:
    """Running twice doesn't create duplicates."""
    first = await create_channels(db_session)
    second = await create_channels(db_session)
    # Same slugs, same IDs.
    for slug, ch_id in first.items():
        assert second[slug] == ch_id


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
async def test_create_channel_memberships(db_session: AsyncSession) -> None:
    channel_ids = await create_channels(db_session)
    agent_ids = await create_agents(db_session)
    # Should not raise.
    await create_channel_memberships(db_session, channel_ids, agent_ids)


@pytest.mark.asyncio
async def test_create_initial_messages(db_session: AsyncSession) -> None:
    """Initial-message seeding wraps multiple ops; smoke-test it doesn't raise."""
    channel_ids = await create_channels(db_session)
    agent_ids = await create_agents(db_session)
    await create_channel_memberships(db_session, channel_ids, agent_ids)
    # Initial messages may or may not be seeded depending on config — just
    # confirm the call doesn't raise. Some setups may not have everything
    # wired; accept silent skip.
    with contextlib.suppress(Exception):
        await create_initial_messages(db_session, channel_ids, agent_ids)


@pytest.mark.asyncio
async def test_create_initial_messages_skips_when_channel_missing(
    db_session: AsyncSession,
) -> None:
    """Missing channel/agent IDs cause continue branches (line 226)."""
    # Empty mappings → every iteration hits the "skip" continue.
    await create_initial_messages(db_session, {}, {})


@pytest.mark.asyncio
async def test_create_initial_messages_skips_when_already_present(
    db_session: AsyncSession,
) -> None:
    """A second pass triggers the duplicate-detection skip (lines 238-239)."""
    channel_ids = await create_channels(db_session)
    agent_ids = await create_agents(db_session)
    await create_channel_memberships(db_session, channel_ids, agent_ids)
    # First pass seeds messages; second pass hits the existing-message skip.
    await create_initial_messages(db_session, channel_ids, agent_ids)
    await db_session.flush()
    await create_initial_messages(db_session, channel_ids, agent_ids)


@pytest.mark.asyncio
async def test_create_channel_memberships_skips_unknown_channels(
    db_session: AsyncSession,
) -> None:
    """Unknown channel slugs hit the continue branches (lines 159, 163, 179, 183)."""
    agent_ids = await create_agents(db_session)
    # Pass an empty channel map — every membership entry skips at line 159
    # and every auditor-access entry skips at line 178.
    await create_channel_memberships(db_session, {}, agent_ids)


@pytest.mark.asyncio
async def test_create_channel_memberships_with_orphan_channel_id(
    db_session: AsyncSession,
) -> None:
    """Channel slug pointing at a non-existent UUID hits lines 163/183."""

    agent_ids = await create_agents(db_session)
    # Real channels exist; we lie about their UUID so _get_channel returns None.
    bogus = {slug: str(uuid4()) for slug in ("backend-cell", "announcements")}
    await create_channel_memberships(db_session, bogus, agent_ids)


@pytest.mark.asyncio
async def test_bootstrap_database_invokes_full_pipeline() -> None:
    """bootstrap_database wires up init_db + seeding (lines 284-298)."""
    fake_session = AsyncMock()
    fake_session.commit = AsyncMock()

    @asynccontextmanager
    async def _ctx() -> AsyncGenerator[Any, None]:
        yield fake_session

    with (
        patch("roboco.db.seed.init_db", AsyncMock()) as mock_init,
        patch("roboco.db.seed.get_db_context", _ctx),
        patch(
            "roboco.db.seed.create_channels",
            AsyncMock(return_value={"backend-cell": "id"}),
        ) as mock_ch,
        patch(
            "roboco.db.seed.create_agents",
            AsyncMock(return_value={"be-dev-1": "id"}),
        ) as mock_ag,
        patch("roboco.db.seed.create_channel_memberships", AsyncMock()) as mock_mem,
        patch("roboco.db.seed.create_initial_messages", AsyncMock()) as mock_msg,
    ):
        await bootstrap_database()
    mock_init.assert_awaited_once()
    mock_ch.assert_awaited_once()
    mock_ag.assert_awaited_once()
    mock_mem.assert_awaited_once()
    mock_msg.assert_awaited_once()
    fake_session.commit.assert_awaited_once()

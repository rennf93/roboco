"""Coverage for `permissions.has_privileged_access` and `permissions.is_pm_role`."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.services.permissions import has_privileged_access, is_pm_role

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _make_agent(role: AgentRole, team: Team | None = None) -> AgentTable:
    return AgentTable(
        id=uuid4(),
        name=f"agent-{uuid4().hex[:6]}",
        slug=f"a-{uuid4().hex[:6]}",
        role=role,
        team=team,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )


# ---------------------------------------------------------------------------
# has_privileged_access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_has_privileged_access_ceo_true(db_session: AsyncSession) -> None:
    agent = _make_agent(AgentRole.CEO)
    db_session.add(agent)
    await db_session.flush()
    assert await has_privileged_access(db_session, cast("uuid.UUID", agent.id)) is True


@pytest.mark.asyncio
async def test_has_privileged_access_developer_false(
    db_session: AsyncSession,
) -> None:
    agent = _make_agent(AgentRole.DEVELOPER, Team.BACKEND)
    db_session.add(agent)
    await db_session.flush()
    assert await has_privileged_access(db_session, cast("uuid.UUID", agent.id)) is False


@pytest.mark.asyncio
async def test_has_privileged_access_unknown_id_false(
    db_session: AsyncSession,
) -> None:
    """Unknown agent id → False."""
    assert await has_privileged_access(db_session, uuid4()) is False


# ---------------------------------------------------------------------------
# is_pm_role (lines 456-457)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_pm_role_main_pm_true(db_session: AsyncSession) -> None:
    agent = _make_agent(AgentRole.MAIN_PM)
    db_session.add(agent)
    await db_session.flush()
    assert await is_pm_role(db_session, cast("uuid.UUID", agent.id)) is True


@pytest.mark.asyncio
async def test_is_pm_role_developer_false(db_session: AsyncSession) -> None:
    agent = _make_agent(AgentRole.DEVELOPER, Team.BACKEND)
    db_session.add(agent)
    await db_session.flush()
    assert await is_pm_role(db_session, cast("uuid.UUID", agent.id)) is False


@pytest.mark.asyncio
async def test_is_pm_role_ceo_true(db_session: AsyncSession) -> None:
    agent = _make_agent(AgentRole.CEO)
    db_session.add(agent)
    await db_session.flush()
    assert await is_pm_role(db_session, cast("uuid.UUID", agent.id)) is True


@pytest.mark.asyncio
async def test_is_pm_role_unknown_id_false(db_session: AsyncSession) -> None:
    """Unknown agent id → False (covers role is None branch on line 456-457)."""
    assert await is_pm_role(db_session, uuid4()) is False

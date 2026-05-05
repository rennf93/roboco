"""api.utils.resources coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi import HTTPException
from roboco.api.utils.resources import (
    get_by_field_or_404,
    get_or_404,
    require_membership,
    require_ownership,
    require_recipient,
)
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_get_or_404_finds_existing(db_session: AsyncSession) -> None:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"d-{uuid4().hex[:8]}",
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

    fetched = await get_or_404(db_session, AgentTable, agent.id)
    assert fetched.id == agent.id


@pytest.mark.asyncio
async def test_get_or_404_raises_when_missing(db_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc:
        await get_or_404(db_session, AgentTable, uuid4(), "Agent")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_by_field_or_404_finds(db_session: AsyncSession) -> None:
    agent = AgentTable(
        id=uuid4(),
        name="Dev2",
        slug=f"d2-{uuid4().hex[:8]}",
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

    fetched = await get_by_field_or_404(
        db_session, AgentTable, "slug", agent.slug, "Agent"
    )
    assert fetched.id == agent.id


@pytest.mark.asyncio
async def test_get_by_field_or_404_raises(db_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc:
        await get_by_field_or_404(db_session, AgentTable, "slug", "ghost-slug", "Agent")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Ownership / Recipients / Membership
# ---------------------------------------------------------------------------


def test_require_ownership_passes_for_owner() -> None:
    aid = uuid4()
    resource = type("R", (), {"owner": aid})()
    require_ownership(resource, "owner", aid, "edit")


def test_require_ownership_raises_for_other_agent() -> None:
    resource = type("R", (), {"owner": uuid4()})()
    with pytest.raises(HTTPException) as exc:
        require_ownership(resource, "owner", uuid4(), "edit")
    assert exc.value.status_code == 403


def test_require_ownership_no_owner_passes() -> None:
    resource = type("R", (), {"owner": None})()
    # Should not raise — no owner means open access.
    require_ownership(resource, "owner", uuid4(), "edit")


def test_require_recipient_passes() -> None:
    aid = uuid4()
    require_recipient([uuid4(), aid, uuid4()], aid)


def test_require_recipient_raises() -> None:
    with pytest.raises(HTTPException) as exc:
        require_recipient([uuid4(), uuid4()], uuid4())
    assert exc.value.status_code == 403


def test_require_membership_passes() -> None:
    aid = uuid4()
    require_membership([uuid4(), aid], aid, "channel")


def test_require_membership_raises() -> None:
    with pytest.raises(HTTPException) as exc:
        require_membership([uuid4()], uuid4(), "channel")
    assert exc.value.status_code == 403

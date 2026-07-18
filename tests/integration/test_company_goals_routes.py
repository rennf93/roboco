"""Company-goals API route tests: GET open to any agent, PUT CEO-only."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from roboco.api.routes.company_goals import get_company_goals, update_company_goals
from roboco.api.schemas.company_goals import CompanyGoalsUpdate
from roboco.db.tables import CompanyGoalsTable
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from sqlalchemy import delete

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _agent(role: AgentRole) -> AgentContext:
    return AgentContext(agent_id=uuid4(), role=role, team=None)


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_company_goals(db_session: Any) -> AsyncIterator[None]:
    yield
    # update_company_goals() calls db.commit() (real behavior — see the route
    # docstring), so a CEO update here persists past the per-test rollback
    # into every later test in this session-scoped DB run. Delete the
    # singleton row so it reads back to empty defaults for whatever runs
    # next (mirrors the ceo_client teardown in test_release_routes.py).
    await db_session.execute(delete(CompanyGoalsTable))
    await db_session.commit()


@pytest.mark.asyncio
async def test_get_returns_charter_to_any_agent(db_session: Any) -> None:
    resp = await get_company_goals(db_session, _agent(AgentRole.DEVELOPER))
    assert resp.north_star == ""
    assert resp.objectives == []


@pytest.mark.asyncio
async def test_ceo_can_update_and_persist(db_session: Any) -> None:
    ceo = _agent(AgentRole.CEO)
    resp = await update_company_goals(
        CompanyGoalsUpdate(north_star="Win the market"), db_session, ceo
    )
    assert resp.north_star == "Win the market"
    assert resp.updated_by == str(ceo.agent_id)
    # Persisted and readable by a non-CEO agent.
    again = await get_company_goals(db_session, _agent(AgentRole.QA))
    assert again.north_star == "Win the market"


@pytest.mark.asyncio
async def test_ceo_can_update_and_persist_brand_voice(db_session: Any) -> None:
    ceo = _agent(AgentRole.CEO)
    resp = await update_company_goals(
        CompanyGoalsUpdate(brand_voice="Confident, dry wit."), db_session, ceo
    )
    assert resp.brand_voice == "Confident, dry wit."
    # Persisted and readable by a non-CEO agent (round-trips through the
    # Pydantic response schema, not just the service-layer dict).
    again = await get_company_goals(db_session, _agent(AgentRole.QA))
    assert again.brand_voice == "Confident, dry wit."


@pytest.mark.asyncio
async def test_ceo_can_update_and_persist_company_name(db_session: Any) -> None:
    ceo = _agent(AgentRole.CEO)
    resp = await update_company_goals(
        CompanyGoalsUpdate(company_name="Acme Robotics"), db_session, ceo
    )
    assert resp.company_name == "Acme Robotics"
    # Persisted and readable by a non-CEO agent (round-trips through the
    # Pydantic response schema, not just the service-layer dict).
    again = await get_company_goals(db_session, _agent(AgentRole.QA))
    assert again.company_name == "Acme Robotics"


@pytest.mark.asyncio
async def test_non_ceo_cannot_update() -> None:
    # The CEO check fires before any DB access, so a dummy session suffices.
    with pytest.raises(HTTPException) as exc:
        await update_company_goals(
            CompanyGoalsUpdate(north_star="nope"),
            MagicMock(),
            _agent(AgentRole.DEVELOPER),
        )
    assert exc.value.status_code == HTTPStatus.FORBIDDEN

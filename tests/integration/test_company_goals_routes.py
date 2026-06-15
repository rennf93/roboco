"""Company-goals API route tests: GET open to any agent, PUT CEO-only."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from roboco.api.routes.company_goals import get_company_goals, update_company_goals
from roboco.api.schemas.company_goals import CompanyGoalsUpdate
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext


def _agent(role: AgentRole) -> AgentContext:
    return AgentContext(agent_id=uuid4(), role=role, team=None)


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
async def test_non_ceo_cannot_update() -> None:
    # The CEO check fires before any DB access, so a dummy session suffices.
    with pytest.raises(HTTPException) as exc:
        await update_company_goals(
            CompanyGoalsUpdate(north_star="nope"),
            MagicMock(),
            _agent(AgentRole.DEVELOPER),
        )
    assert exc.value.status_code == HTTPStatus.FORBIDDEN

"""Unit tests for AgentOverviewService (status + spend join)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.services.agent_overview import AgentOverviewService

_COST = 1.5


def _agent(slug: str, status: AgentStatus, task_id: object = None) -> SimpleNamespace:
    return SimpleNamespace(
        slug=slug,
        name=slug.upper(),
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=status,
        current_task_id=task_id,
    )


@pytest.mark.asyncio
async def test_overview_joins_status_spend_and_rate_limits() -> None:
    task_id = uuid4()
    agents = [
        _agent("be-dev-1", AgentStatus.IDLE),
        _agent("be-dev-2", AgentStatus.ACTIVE, task_id),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = agents
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    spend = [{"agent_slug": "be-dev-2", "total_tokens": 10, "cost_usd": _COST}]

    with (
        patch(
            "roboco.services.agent_overview.UsageService.get_by_agent",
            new=AsyncMock(return_value=spend),
        ),
        patch(
            "roboco.services.agent_overview.RateLimitStateTracker."
            "list_rate_limited_providers",
            new=AsyncMock(return_value=[("anthropic", {})]),
        ),
    ):
        out = await AgentOverviewService(session).overview("7d")

    assert out["period"] == "7d"
    assert out["total"] == len(agents)
    assert out["by_status"][AgentStatus.ACTIVE.value] == 1
    assert out["total_cost_usd"] == _COST
    assert out["rate_limited_providers"] == ["anthropic"]
    dev2 = next(a for a in out["agents"] if a["slug"] == "be-dev-2")
    assert dev2["current_task_id"] == str(task_id)
    assert dev2["cost_usd"] == _COST
    dev1 = next(a for a in out["agents"] if a["slug"] == "be-dev-1")
    assert dev1["cost_usd"] == 0.0
    assert dev1["current_task_id"] is None

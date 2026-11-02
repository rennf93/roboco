"""Model routing that cannot reach the DB falls back to the legacy Anthropic
route for the agent's role, so a spawn never stalls on routing."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from roboco.models.base import ModelProvider
from roboco.models.runtime import MODEL_MAP, ROLE_MODEL_MAP
from roboco.runtime.orchestrator import AgentOrchestrator


def _orchestrator_without_io() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        return AgentOrchestrator.__new__(AgentOrchestrator)


@pytest.mark.asyncio
async def test_unreachable_routing_db_falls_back_to_role_default_anthropic() -> None:
    orch = _orchestrator_without_io()
    with patch(
        "roboco.db.base.get_session_factory",
        side_effect=RuntimeError("database unavailable"),
    ):
        route = await orch._resolve_agent_route("be-dev-1")

    assert route.provider_type is ModelProvider.ANTHROPIC
    assert route.provider_id is None
    assert route.base_url is None
    assert route.auth_token is None
    assert route.model_name == MODEL_MAP[ROLE_MODEL_MAP["developer"]]

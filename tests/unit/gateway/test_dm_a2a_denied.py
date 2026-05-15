"""Smoke-7: dm catches A2AAccessDeniedError as Envelope.not_authorized.

Original bug: be-qa called dm(recipient='qa-all', ...) — 'qa-all' is a
channel slug, not an agent slug. A2A enforcement raised
A2AAccessDeniedError. It propagated past dm(), past content_actions,
and got caught by FastAPI's middleware which renders RobocoError.to_dict()
as `{'error': {'code': ..., 'message': ..., 'details': ...}}`.

do_server's circuit-breaker check then did
`dict_error in _CIRCUIT_REJECTION_KINDS` and crashed with
`TypeError: unhashable type: 'dict'`. The agent saw a generic
"Error executing tool dm: unhashable type: 'dict'" and got stuck.

The do_server defense-in-depth test lives in
tests/unit/mcp_servers/test_do_server_circuit_breaker.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.enforcement.a2a_access import A2AAccessDeniedError
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


def _make_deps(**overrides: object) -> ContentActionsDeps:
    base: dict[str, object] = {
        "task": AsyncMock(),
        "git": AsyncMock(),
        "messaging": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "workspace": AsyncMock(),
        "notifications": AsyncMock(),
    }
    base.update(overrides)
    return ContentActionsDeps(**base)


@pytest.mark.asyncio
async def test_dm_a2a_denied_returns_envelope_not_authorized() -> None:
    """A2AAccessDeniedError is caught and returned as Envelope.not_authorized."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(id=task_id, status="in_progress", assigned_to=agent_id)

    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="qa")
    task_svc.get_journal_context_task_for_agent.return_value = task_obj
    task_svc.get_active_task_for_agent.return_value = task_obj
    task_svc.get.return_value = task_obj

    a2a_svc = AsyncMock()
    a2a_svc.send.side_effect = A2AAccessDeniedError(
        from_agent="be-qa",
        to_agent="qa-all",
        reason="Cannot A2A unknown. Route: be-qa → be-pm → main-pm.",
        route_hint="be-qa → be-pm → main-pm",
    )

    deps = _make_deps(task=task_svc, a2a=a2a_svc)
    actions = ContentActions(deps)

    env = await actions.dm(
        agent_id=agent_id,
        recipient="qa-all",
        text="PASS notice",
        task_id=task_id,
    )

    assert env.error == "not_authorized", (
        f"A2A denial must surface as not_authorized envelope, got {env.error!r}. "
        "If it escapes to FastAPI middleware, RobocoError.to_dict() renders the "
        "error as a dict and the do_server circuit breaker crashes."
    )
    assert "be-qa" in env.message
    assert env.remediate is not None

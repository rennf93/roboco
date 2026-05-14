"""Smoke-4: open_session must pass the model schema (not the API schema) to
the messaging service.

Smoke run 4 surfaced an AttributeError because content_actions.open_session
constructed a `SessionForTasksCreateRequest` (API schema, flat fields) but
`MessagingService._build_session_request` reads `req.config.max_message_count`
(the model has nested `config`). Two schemas, one expected, one passed.

This test pins the contract: the gateway constructs the model and the
service receives the model.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.session import SessionForTasksCreate, SessionTaskRelationshipType
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
async def test_open_session_passes_model_schema_to_service() -> None:
    """The service must receive a SessionForTasksCreate (model), not the API schema."""
    agent_id = uuid4()
    task_id = uuid4()
    pm_agent = MagicMock(role="cell_pm")

    task_svc = AsyncMock()
    task_svc.agent_for.return_value = pm_agent

    session_row = MagicMock(id=uuid4())
    messaging_svc = AsyncMock()
    messaging_svc.create_session_for_tasks.return_value = (session_row, [])

    deps = _make_deps(task=task_svc, messaging=messaging_svc)
    actions = ContentActions(deps)

    env = await actions.open_session(
        agent_id=agent_id,
        task_id=task_id,
        channel="backend-cell",
        topic="discuss the plan",
        relationship_type="discussion",
        group_id=None,
    )

    assert env.error is None, (
        f"open_session returned error: {env.error} / {env.message}"
    )
    messaging_svc.create_session_for_tasks.assert_awaited_once()
    call = messaging_svc.create_session_for_tasks.await_args
    passed_req = call.kwargs["req"]
    assert isinstance(passed_req, SessionForTasksCreate), (
        f"expected SessionForTasksCreate (model), got {type(passed_req).__name__}. "
        f"The service reads req.config.* which only exists on the model."
    )


@pytest.mark.asyncio
async def test_open_session_unknown_relationship_falls_back_to_discussion() -> None:
    """Invalid relationship_type string defaults to DISCUSSION enum."""
    pm_agent = MagicMock(role="main_pm")
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = pm_agent

    session_row = MagicMock(id=uuid4())
    messaging_svc = AsyncMock()
    messaging_svc.create_session_for_tasks.return_value = (session_row, [])

    deps = _make_deps(task=task_svc, messaging=messaging_svc)
    actions = ContentActions(deps)

    await actions.open_session(
        agent_id=uuid4(),
        task_id=uuid4(),
        channel="backend-cell",
        topic="discuss",
        relationship_type="nonsense-type-not-in-enum",
        group_id=None,
    )

    passed_req = messaging_svc.create_session_for_tasks.await_args.kwargs["req"]
    assert passed_req.relationship_type == SessionTaskRelationshipType.DISCUSSION

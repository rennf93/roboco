"""roboco.services.gateway.content_actions.propose_feature_spotlight — HoM-gated
feature-spotlight draft authoring."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


class _FakeTask:
    """Minimal stand-in for the ORM TaskTable row — carries just what
    ``propose_feature_spotlight`` touches."""

    def __init__(self, *, assigned_to: Any, task_id: Any = None) -> None:
        self.id = task_id or uuid4()
        self.assigned_to = assigned_to
        self.project_id = uuid4()


def _actions(role: str) -> ContentActions:
    task = MagicMock()
    agent = MagicMock()
    agent.role = role
    task.agent_for = AsyncMock(return_value=agent)
    task.session = MagicMock()
    deps = ContentActionsDeps(
        task=task,
        git=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        workspace=MagicMock(),
        notifications=MagicMock(),
    )
    return ContentActions(deps)


def _valid_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "feature_slug": "org-memory",
        "feature_title": "Organizational Memory Loop",
        "body": "Did you know RoboCo agents learn from every completed task?",
    }
    kwargs.update(overrides)
    return kwargs


@pytest.mark.asyncio
async def test_propose_feature_spotlight_forbidden_for_product_owner() -> None:
    env = await _actions("product_owner").propose_feature_spotlight(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_forbidden_for_developer() -> None:
    env = await _actions("developer").propose_feature_spotlight(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_rejects_short_slug() -> None:
    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=uuid4(), **_valid_kwargs(feature_slug="a")
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_rejects_short_title() -> None:
    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=uuid4(), **_valid_kwargs(feature_title="ab")
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_rejects_short_body() -> None:
    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=uuid4(), **_valid_kwargs(body="short")
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_rejects_over_280_chars() -> None:
    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=uuid4(), **_valid_kwargs(body="z" * 281)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_no_open_exploration_is_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_svc = MagicMock()
    task_svc.list_open_feature_explorations = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_ignores_exploration_assigned_to_another_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_agent = uuid4()
    exploration = _FakeTask(assigned_to=other_agent)
    task_svc = MagicMock()
    task_svc.list_open_feature_explorations = AsyncMock(return_value=[exploration])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_rejects_already_seen_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    exploration = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_feature_explorations = AsyncMock(return_value=[exploration])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)

    engine = MagicMock()
    engine.is_feature_seen = AsyncMock(return_value=True)
    monkeypatch.setattr("roboco.services.x_engine.get_x_engine", lambda _s: engine)

    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=agent_id, **_valid_kwargs()
    )
    assert env.error == "invalid_state"
    engine.materialize_feature_spotlight.assert_not_called()


@pytest.mark.asyncio
async def test_propose_feature_spotlight_materializes_new_draft_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path — deliberately asymmetric vs. propose_roadmap: the returned
    task_id is the NEW materialized draft's id, never the exploration task's."""
    agent_id = uuid4()
    exploration = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_feature_explorations = AsyncMock(return_value=[exploration])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)

    materialized = _FakeTask(assigned_to=agent_id)
    assert materialized.id != exploration.id
    engine = MagicMock()
    engine.is_feature_seen = AsyncMock(return_value=False)
    engine.materialize_feature_spotlight = AsyncMock(return_value=materialized)
    monkeypatch.setattr("roboco.services.x_engine.get_x_engine", lambda _s: engine)

    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=agent_id, **_valid_kwargs()
    )
    assert env.error is None
    assert env.status == "feature_spotlight_proposed"
    assert env.task_id == str(materialized.id)
    assert env.task_id != str(exploration.id)
    engine.materialize_feature_spotlight.assert_awaited_once_with(
        exploration_task=exploration,
        feature_slug="org-memory",
        feature_title="Organizational Memory Loop",
        body="Did you know RoboCo agents learn from every completed task?",
    )

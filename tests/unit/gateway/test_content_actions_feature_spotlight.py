"""roboco.services.gateway.content_actions.propose_feature_spotlight — HoM-gated
feature-spotlight draft authoring."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings as cfg
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


# --------------------------------------------------------------------------- #
# wants_video companion — additive, default-False, best-effort
# --------------------------------------------------------------------------- #


def _mock_spotlight_materialization(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Wire an open exploration + a materializing XEngine, mirroring the happy
    path above, so wants_video tests only need to stub the video engine."""
    agent_id = uuid4()
    exploration = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_feature_explorations = AsyncMock(return_value=[exploration])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)

    materialized = _FakeTask(assigned_to=agent_id)
    x_engine = MagicMock()
    x_engine.is_feature_seen = AsyncMock(return_value=False)
    x_engine.materialize_feature_spotlight = AsyncMock(return_value=materialized)
    monkeypatch.setattr("roboco.services.x_engine.get_x_engine", lambda _s: x_engine)
    return agent_id


def _mock_video_engine(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    video_engine = MagicMock()
    video_engine.open_video_task = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        "roboco.services.video_engine.get_video_engine", lambda _s: video_engine
    )
    return video_engine


def _enable_video(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "video_engine_enabled", True)
    monkeypatch.setattr(cfg, "video_on_spotlight", True)


@pytest.mark.asyncio
async def test_propose_feature_spotlight_wants_video_opens_video_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_video(monkeypatch)
    agent_id = _mock_spotlight_materialization(monkeypatch)
    video_engine = _mock_video_engine(monkeypatch)

    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=agent_id, **_valid_kwargs(), wants_video=True
    )

    assert env.error is None
    video_engine.open_video_task.assert_awaited_once()
    kwargs = video_engine.open_video_task.call_args.kwargs
    assert kwargs["occasion"] == "spotlight org-memory"
    assert kwargs["platforms"] == ["x", "tiktok"]
    expected_brief = (
        "Organizational Memory Loop: Did you know RoboCo agents learn from "
        "every completed task?"
    )
    assert kwargs["brief"] == expected_brief
    assert kwargs["script"] == expected_brief  # falls back — no video_script given


@pytest.mark.asyncio
async def test_propose_feature_spotlight_wants_video_uses_explicit_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_video(monkeypatch)
    agent_id = _mock_spotlight_materialization(monkeypatch)
    video_engine = _mock_video_engine(monkeypatch)

    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=agent_id,
        **_valid_kwargs(),
        wants_video=True,
        video_script="Custom voiceover script",
    )

    assert env.error is None
    kwargs = video_engine.open_video_task.call_args.kwargs
    assert kwargs["script"] == "Custom voiceover script"
    assert kwargs["brief"] != "Custom voiceover script"  # brief is always title:body


@pytest.mark.asyncio
async def test_propose_feature_spotlight_default_wants_video_false_skips_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default False -> byte-for-byte unchanged spotlight behavior: the video
    engine is never even looked up."""
    _enable_video(monkeypatch)
    agent_id = _mock_spotlight_materialization(monkeypatch)
    video_engine = _mock_video_engine(monkeypatch)

    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=agent_id, **_valid_kwargs()
    )

    assert env.error is None
    assert env.status == "feature_spotlight_proposed"
    video_engine.open_video_task.assert_not_called()


@pytest.mark.asyncio
async def test_propose_feature_spotlight_wants_video_but_flags_off_skips_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "video_engine_enabled", False)
    monkeypatch.setattr(cfg, "video_on_spotlight", False)
    agent_id = _mock_spotlight_materialization(monkeypatch)
    video_engine = _mock_video_engine(monkeypatch)

    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=agent_id, **_valid_kwargs(), wants_video=True
    )

    assert env.error is None
    video_engine.open_video_task.assert_not_called()


@pytest.mark.asyncio
async def test_propose_feature_spotlight_video_failure_does_not_break_spotlight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Best-effort: a video-engine blow-up must not surface as an error on the
    spotlight verb — the spotlight draft already materialized."""
    _enable_video(monkeypatch)
    agent_id = _mock_spotlight_materialization(monkeypatch)
    monkeypatch.setattr(
        "roboco.services.video_engine.get_video_engine",
        MagicMock(side_effect=RuntimeError("video-engine boom")),
    )

    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=agent_id, **_valid_kwargs(), wants_video=True
    )

    assert env.error is None
    assert env.status == "feature_spotlight_proposed"

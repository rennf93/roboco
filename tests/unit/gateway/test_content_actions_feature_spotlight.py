"""roboco.services.gateway.content_actions.propose_feature_spotlight — HoM-gated
feature-spotlight draft authoring."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings as cfg
from roboco.foundation.policy.content import markers
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
# wants_video companion — additive, default-False. Task 4 (2026-07-09 pipeline
# fixes) moved the actual video-authoring open OFF authoring time and onto
# XPostService.approve (see test_x_post_service.py), so this verb only stamps
# the request onto the draft's x_feature_ref marker and never touches the
# video engine itself, regardless of wants_video or the video flags.
# --------------------------------------------------------------------------- #


def _mock_spotlight_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Any]:
    """Wire an open exploration + a materializing XEngine, mirroring the happy
    path above, so wants_video tests only need to inspect the returned draft's
    marker (or stub the video engine to prove it's never called)."""
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
    return agent_id, materialized


def _actions_with_flushable_session(role: str) -> ContentActions:
    """``_actions`` with ``task.session.flush`` made awaitable — needed once
    ``wants_video`` triggers the marker-write flush in
    ``propose_feature_spotlight`` (mirrors the same pattern in
    test_content_actions_roadmap.py)."""
    actions = _actions(role)
    actions.task.session.flush = AsyncMock()
    return actions


@pytest.mark.asyncio
async def test_propose_feature_spotlight_never_opens_video_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with both video flags on and wants_video=True, this verb never
    opens a video-authoring task — that now happens at CEO-approve time."""
    monkeypatch.setattr(cfg, "video_engine_enabled", True)
    monkeypatch.setattr(cfg, "video_on_spotlight", True)
    agent_id, _materialized = _mock_spotlight_materialization(monkeypatch)
    video_engine = MagicMock()
    video_engine.open_video_task = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        "roboco.services.video_engine.get_video_engine", lambda _s: video_engine
    )

    env = await _actions_with_flushable_session(
        "head_marketing"
    ).propose_feature_spotlight(agent_id=agent_id, **_valid_kwargs(), wants_video=True)

    assert env.error is None
    video_engine.open_video_task.assert_not_called()


@pytest.mark.asyncio
async def test_propose_feature_spotlight_wants_video_stamps_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id, materialized = _mock_spotlight_materialization(monkeypatch)

    env = await _actions_with_flushable_session(
        "head_marketing"
    ).propose_feature_spotlight(
        agent_id=agent_id,
        **_valid_kwargs(),
        wants_video=True,
        video_script="Custom voiceover script",
    )

    assert env.error is None
    ref = markers.get_x_feature_ref(materialized)
    assert ref is not None
    assert ref["slug"] == "org-memory"
    assert ref["title"] == "Organizational Memory Loop"
    assert ref["wants_video"] is True
    assert ref["video_script"] == "Custom voiceover script"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_wants_video_without_script_stores_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No explicit script -> stored as "" (the fallback-to-brief logic lives
    in XPostService._open_spotlight_video at approve time, not here)."""
    agent_id, materialized = _mock_spotlight_materialization(monkeypatch)

    env = await _actions_with_flushable_session(
        "head_marketing"
    ).propose_feature_spotlight(agent_id=agent_id, **_valid_kwargs(), wants_video=True)

    assert env.error is None
    ref = markers.get_x_feature_ref(materialized)
    assert ref is not None
    assert ref["video_script"] == ""


@pytest.mark.asyncio
async def test_propose_feature_spotlight_default_wants_video_false_leaves_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default False -> byte-for-byte unchanged: this verb never re-touches
    the x_feature_ref marker at all."""
    agent_id, materialized = _mock_spotlight_materialization(monkeypatch)

    env = await _actions_with_flushable_session(
        "head_marketing"
    ).propose_feature_spotlight(agent_id=agent_id, **_valid_kwargs())

    assert env.error is None
    assert env.status == "feature_spotlight_proposed"
    assert markers.get_x_feature_ref(materialized) is None


# --------------------------------------------------------------------------- #
# skip — the HoM "nothing worth spotlighting this cycle" exit
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_feature_spotlight_skip_forbidden_for_product_owner() -> None:
    env = await _actions("product_owner").propose_feature_spotlight(
        agent_id=uuid4(), skip=True, skip_reason="nothing shipped worth spotlighting"
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_skip_requires_reason() -> None:
    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=uuid4(), skip=True
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_skip_rejects_short_reason() -> None:
    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=uuid4(), skip=True, skip_reason="meh"
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_skip_no_open_exploration_is_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_svc = MagicMock()
    task_svc.list_open_feature_explorations = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=uuid4(), skip=True, skip_reason="nothing shipped worth spotlighting"
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_feature_spotlight_skip_completes_exploration_without_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    exploration = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_feature_explorations = AsyncMock(return_value=[exploration])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)

    engine = MagicMock()
    engine.skip_feature_spotlight = AsyncMock(return_value=exploration)
    monkeypatch.setattr("roboco.services.x_engine.get_x_engine", lambda _s: engine)

    reason = "nothing shipped worth spotlighting this cycle"
    env = await _actions("head_marketing").propose_feature_spotlight(
        agent_id=agent_id, skip=True, skip_reason=reason
    )

    assert env.error is None
    assert env.status == "feature_spotlight_skipped"
    assert env.task_id == str(exploration.id)
    engine.skip_feature_spotlight.assert_awaited_once_with(
        exploration_task=exploration, reason=reason
    )
    engine.materialize_feature_spotlight.assert_not_called()


@pytest.mark.asyncio
async def test_propose_feature_spotlight_missing_fields_is_invalid_state() -> None:
    """Defaulting feature_slug/feature_title/body to "" (so skip=True never
    forces dummy values) must not weaken non-skip validation."""
    env = await _actions("head_marketing").propose_feature_spotlight(agent_id=uuid4())
    assert env.error == "invalid_state"

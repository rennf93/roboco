"""roboco.services.gateway.content_actions.propose_campaign — Head-of-
Marketing-gated War Room campaign authoring. Mirrors
test_content_actions_coroner.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps

THREE = 3


class _FakeTask:
    """Minimal stand-in for the ORM TaskTable row — carries just what
    ``propose_campaign`` touches."""

    def __init__(self, *, assigned_to: Any) -> None:
        self.id = uuid4()
        self.assigned_to = assigned_to
        self.orchestration_markers: dict[str, Any] | None = None
        self.status = TaskStatus.PENDING


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
        notification_delivery=None,
    )
    return ContentActions(deps)


def _future(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def _post(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "body": "RoboCo v0.30.0 teaser drops soon.",
        "publish_after": _future(1),
        "stage_label": "teaser",
    }
    base.update(overrides)
    return base


def _valid_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agent_id": uuid4(),
        "campaign_name": "v0.30.0 launch",
        "posts": [
            _post(stage_label="teaser", publish_after=_future(1)),
            _post(stage_label="launch", publish_after=_future(2)),
        ],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_propose_campaign_forbidden_for_non_hom() -> None:
    env = await _actions("product_owner").propose_campaign(**_valid_kwargs())
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_campaign_forbidden_for_auditor() -> None:
    env = await _actions("auditor").propose_campaign(**_valid_kwargs())
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_campaign_rejects_short_name() -> None:
    env = await _actions("head_marketing").propose_campaign(
        **_valid_kwargs(campaign_name="ab")
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_campaign_rejects_oversized_name() -> None:
    env = await _actions("head_marketing").propose_campaign(
        **_valid_kwargs(campaign_name="x" * 101)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_campaign_rejects_single_post() -> None:
    env = await _actions("head_marketing").propose_campaign(
        **_valid_kwargs(posts=[_post()])
    )
    assert env.error == "invalid_state"
    assert "2-6" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_campaign_rejects_seven_posts() -> None:
    posts = [_post(publish_after=_future(i + 1)) for i in range(7)]
    env = await _actions("head_marketing").propose_campaign(
        **_valid_kwargs(posts=posts)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_campaign_accepts_six_posts_shape() -> None:
    """Six is the top of the valid range — this must clear the count gate
    (the "no open cycle" rejection proves the count check itself passed)."""
    posts = [_post(publish_after=_future(i + 1)) for i in range(6)]
    actions = _actions("head_marketing")
    with patch("roboco.services.task.get_task_service") as get_task_service:
        task_svc = MagicMock()
        task_svc.list_open_war_room_cycles = AsyncMock(return_value=[])
        get_task_service.return_value = task_svc
        env = await actions.propose_campaign(**_valid_kwargs(posts=posts))
    assert env.error == "invalid_state"
    assert "no open war-room exploration task" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_campaign_rejects_oversized_body() -> None:
    env = await _actions("head_marketing").propose_campaign(
        **_valid_kwargs(posts=[_post(body="x" * 281), _post(publish_after=_future(2))])
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_campaign_rejects_unknown_stage_label() -> None:
    env = await _actions("head_marketing").propose_campaign(
        **_valid_kwargs(
            posts=[
                _post(stage_label="not_a_stage"),
                _post(publish_after=_future(2)),
            ]
        )
    )
    assert env.error == "invalid_state"
    assert "stage_label" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_campaign_rejects_past_publish_after() -> None:
    env = await _actions("head_marketing").propose_campaign(
        **_valid_kwargs(
            posts=[
                _post(publish_after=_future(-1)),
                _post(publish_after=_future(2)),
            ]
        )
    )
    assert env.error == "invalid_state"
    assert "future" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_campaign_rejects_non_ascending_publish_after() -> None:
    env = await _actions("head_marketing").propose_campaign(
        **_valid_kwargs(
            posts=[
                _post(publish_after=_future(3)),
                _post(publish_after=_future(1)),
            ]
        )
    )
    assert env.error == "invalid_state"
    assert "ascending" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_campaign_rejects_malformed_publish_after() -> None:
    env = await _actions("head_marketing").propose_campaign(
        **_valid_kwargs(
            posts=[
                _post(publish_after="not-a-date"),
                _post(publish_after=_future(2)),
            ]
        )
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_campaign_no_open_cycle_assigned() -> None:
    actions = _actions("head_marketing")
    with patch("roboco.services.task.get_task_service") as get_task_service:
        task_svc = MagicMock()
        task_svc.list_open_war_room_cycles = AsyncMock(return_value=[])
        get_task_service.return_value = task_svc
        env = await actions.propose_campaign(**_valid_kwargs())
    assert env.error == "invalid_state"
    assert "no open war-room exploration task" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_campaign_materializes_every_post_in_order_and_completes() -> (
    None
):
    agent_id = uuid4()
    task = _FakeTask(assigned_to=agent_id)
    actions = _actions("head_marketing")
    actions.task.session.flush = AsyncMock()
    materialized: list[dict[str, Any]] = []

    async def _materialize(
        *, exploration_task: Any, campaign_ref: dict[str, Any], body: str
    ) -> Any:
        assert exploration_task is task
        drafted = MagicMock()
        drafted.id = uuid4()
        materialized.append({"campaign_ref": campaign_ref, "body": body})
        return drafted

    with (
        patch("roboco.services.task.get_task_service") as get_task_service,
        patch("roboco.services.x_engine.get_x_engine") as get_x_engine,
    ):
        task_svc = MagicMock()
        task_svc.list_open_war_room_cycles = AsyncMock(return_value=[task])
        get_task_service.return_value = task_svc

        engine = MagicMock()
        engine.materialize_campaign_post = AsyncMock(side_effect=_materialize)
        get_x_engine.return_value = engine

        env = await actions.propose_campaign(
            **_valid_kwargs(
                agent_id=agent_id,
                posts=[
                    _post(stage_label="teaser", publish_after=_future(1)),
                    _post(stage_label="launch", publish_after=_future(2)),
                    _post(stage_label="follow_up", publish_after=_future(3)),
                ],
            )
        )

    assert env.status == "campaign_proposed"
    assert env.context_briefing == {
        "campaign_name": "v0.30.0 launch",
        "post_count": THREE,
    }
    assert len(materialized) == THREE
    assert [m["campaign_ref"]["sequence"] for m in materialized] == [1, 2, THREE]
    assert [m["campaign_ref"]["stage_label"] for m in materialized] == [
        "teaser",
        "launch",
        "follow_up",
    ]
    assert all(
        m["campaign_ref"]["campaign_name"] == "v0.30.0 launch" for m in materialized
    )
    assert task.status == TaskStatus.COMPLETED

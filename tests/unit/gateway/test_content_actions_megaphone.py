"""roboco.services.gateway.content_actions.propose_editorial_post — HoM-gated
Megaphone editorial-post authoring. Mirrors
test_content_actions_feature_spotlight.py: same complete-at-propose shape
(the materialized draft's id is returned, never the exploration task's)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


class _FakeTask:
    """Minimal stand-in for the ORM TaskTable row — carries just what
    ``propose_editorial_post`` touches."""

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
        "angle": "dev_log",
        "body": "This week the fleet shipped MegaTask waves and a PR-review gate.",
        "rationale": "Dev-log cadence keeps the audience close to real shipping.",
    }
    kwargs.update(overrides)
    return kwargs


# --------------------------------------------------------------------------- #
# Role gate
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_editorial_post_forbidden_for_product_owner() -> None:
    env = await _actions("product_owner").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_editorial_post_forbidden_for_developer() -> None:
    env = await _actions("developer").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "not_authorized"


# --------------------------------------------------------------------------- #
# Angle vocabulary
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_editorial_post_rejects_unknown_angle() -> None:
    env = await _actions("head_marketing").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs(angle="hot_take")
    )
    assert env.error == "invalid_state"
    assert "angle" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_editorial_post_rejects_empty_angle() -> None:
    env = await _actions("head_marketing").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs(angle="")
    )
    assert env.error == "invalid_state"


@pytest.mark.parametrize(
    "angle", ["dev_log", "behind_scenes", "changelog_highlight", "other"]
)
@pytest.mark.asyncio
async def test_propose_editorial_post_accepts_every_valid_angle(
    angle: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every angle in the vocabulary must clear field validation — proven by
    getting past it to the (empty) open-cycle lookup instead of failing on
    the angle check itself."""
    task_svc = MagicMock()
    task_svc.list_open_megaphone_cycles = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("head_marketing").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs(angle=angle)
    )
    assert env.error == "invalid_state"
    assert "angle" not in (env.message or "")
    assert "no open megaphone exploration" in (env.message or "")


# --------------------------------------------------------------------------- #
# body — 280-char tweet cap + soup
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_editorial_post_rejects_short_body() -> None:
    env = await _actions("head_marketing").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs(body="short")
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_editorial_post_rejects_soup_body() -> None:
    env = await _actions("head_marketing").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs(body="tbd tbd tbd tbd")
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_editorial_post_rejects_over_280_chars() -> None:
    env = await _actions("head_marketing").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs(body="z" * 281)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_editorial_post_accepts_exactly_280_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_svc = MagicMock()
    task_svc.list_open_megaphone_cycles = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    body = "The fleet shipped a lot this week. " * 6
    body = body[:280]
    env = await _actions("head_marketing").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs(body=body)
    )
    assert env.error == "invalid_state"
    assert "no open megaphone exploration" in (env.message or "")


# --------------------------------------------------------------------------- #
# rationale
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_editorial_post_rejects_short_rationale() -> None:
    env = await _actions("head_marketing").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs(rationale="meh")
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_editorial_post_rejects_oversized_rationale() -> None:
    env = await _actions("head_marketing").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs(rationale="x" * 301)
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# Open-cycle lookup
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_editorial_post_no_open_exploration_is_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_svc = MagicMock()
    task_svc.list_open_megaphone_cycles = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("head_marketing").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_editorial_post_ignores_exploration_assigned_to_another_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_agent = uuid4()
    exploration = _FakeTask(assigned_to=other_agent)
    task_svc = MagicMock()
    task_svc.list_open_megaphone_cycles = AsyncMock(return_value=[exploration])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("head_marketing").propose_editorial_post(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# Happy path — the complete-at-propose transition (x_feature asymmetry)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_editorial_post_materializes_new_draft_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path — deliberately asymmetric vs. propose_roadmap: the returned
    task_id is the NEW materialized draft's id, never the exploration task's."""
    agent_id = uuid4()
    exploration = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_megaphone_cycles = AsyncMock(return_value=[exploration])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)

    materialized = _FakeTask(assigned_to=agent_id)
    assert materialized.id != exploration.id
    engine = MagicMock()
    engine.materialize_editorial_post = AsyncMock(return_value=materialized)
    monkeypatch.setattr("roboco.services.x_engine.get_x_engine", lambda _s: engine)

    env = await _actions("head_marketing").propose_editorial_post(
        agent_id=agent_id, **_valid_kwargs()
    )
    assert env.error is None
    assert env.status == "editorial_post_proposed"
    assert env.task_id == str(materialized.id)
    assert env.task_id != str(exploration.id)
    engine.materialize_editorial_post.assert_awaited_once_with(
        exploration_task=exploration,
        angle="dev_log",
        body=_valid_kwargs()["body"],
        rationale=_valid_kwargs()["rationale"],
    )


@pytest.mark.asyncio
async def test_propose_editorial_post_missing_fields_is_invalid_state() -> None:
    env = await _actions("head_marketing").propose_editorial_post(agent_id=uuid4())
    assert env.error == "invalid_state"

"""roboco.services.gateway.content_actions.propose_video — team-gated,
metadata-only video-authoring draft (no render)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


class _FakeTask:
    """Minimal stand-in for the ORM TaskTable row — carries just what
    ``propose_video`` touches."""

    def __init__(
        self,
        *,
        assigned_to: Any,
        source: str = "video",
        task_id: Any = None,
        draft: dict[str, Any] | None = None,
    ) -> None:
        self.id = task_id or uuid4()
        self.assigned_to = assigned_to
        self.source = source
        self.orchestration_markers = {"video_draft": draft} if draft else None


def _actions(role: str, team: str | None) -> ContentActions:
    task = MagicMock()
    agent = MagicMock()
    agent.role = role
    agent.team = team
    task.agent_for = AsyncMock(return_value=agent)
    task.session = MagicMock()
    task.session.flush = AsyncMock()
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
        "composition_id": "release-announcement-v1",
        "x_caption": "We just shipped v1.0.0! Check out the new release.",
        "tiktok_caption": "New release just dropped — here's what's inside.",
        "platforms": ["x", "tiktok"],
    }
    kwargs.update(overrides)
    return kwargs


def _mock_active_task(
    monkeypatch: pytest.MonkeyPatch, task: _FakeTask | None
) -> MagicMock:
    """Stub the caller's currently-active task — the resolver propose_video uses
    (``get_active_task_for_agent``), NOT an oldest-first scan over every open
    video task."""
    task_svc = MagicMock()
    task_svc.get_active_task_for_agent = AsyncMock(return_value=task)
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    return task_svc


# --------------------------------------------------------------------------- #
# team gate
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_video_forbidden_for_backend_dev() -> None:
    env = await _actions("developer", "backend").propose_video(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_video_forbidden_for_frontend_dev() -> None:
    env = await _actions("developer", "frontend").propose_video(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_video_forbidden_with_no_team() -> None:
    env = await _actions("developer", None).propose_video(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "not_authorized"


# --------------------------------------------------------------------------- #
# field validation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_video_rejects_empty_composition_id() -> None:
    env = await _actions("developer", "ux_ui").propose_video(
        agent_id=uuid4(), **_valid_kwargs(composition_id="")
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_video_rejects_over_280_x_caption() -> None:
    env = await _actions("developer", "ux_ui").propose_video(
        agent_id=uuid4(), **_valid_kwargs(x_caption="z" * 281)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_video_rejects_over_2200_tiktok_caption() -> None:
    env = await _actions("developer", "ux_ui").propose_video(
        agent_id=uuid4(), **_valid_kwargs(tiktok_caption="z" * 2201)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_video_rejects_empty_platforms() -> None:
    env = await _actions("developer", "ux_ui").propose_video(
        agent_id=uuid4(), **_valid_kwargs(platforms=[])
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_video_rejects_unknown_platform() -> None:
    env = await _actions("developer", "ux_ui").propose_video(
        agent_id=uuid4(), **_valid_kwargs(platforms=["instagram"])
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_video_rejects_partially_unknown_platform() -> None:
    env = await _actions("developer", "ux_ui").propose_video(
        agent_id=uuid4(), **_valid_kwargs(platforms=["x", "instagram"])
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# authoring-task resolution — the caller's ACTIVE task, not an oldest-first scan
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_video_no_active_task_is_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_active_task(monkeypatch, None)
    env = await _actions("developer", "ux_ui").propose_video(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_video_rejects_when_active_task_not_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dev's currently-active task is an ordinary code task, not a video
    authoring task — refuse rather than write video metadata onto unrelated
    work."""
    agent_id = uuid4()
    other = _FakeTask(assigned_to=agent_id, source="chore")
    _mock_active_task(monkeypatch, other)
    env = await _actions("developer", "ux_ui").propose_video(
        agent_id=agent_id, **_valid_kwargs()
    )
    assert env.error == "invalid_state"
    assert markers.get_video_draft(other) is None


@pytest.mark.asyncio
async def test_propose_video_rejects_when_active_task_is_held_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held video_post draft is never a dev's authoring task (Secretary-owned,
    source video_post) — refused by the source check."""
    agent_id = uuid4()
    held = _FakeTask(assigned_to=agent_id, source="video_post")
    _mock_active_task(monkeypatch, held)
    env = await _actions("developer", "ux_ui").propose_video(
        agent_id=agent_id, **_valid_kwargs()
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_video_targets_active_task_not_an_older_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a UX/UI dev routinely holds more than one open video task
    (finish A -> submit to QA -> claim B). propose_video must write to the task
    the dev is ACTIVELY working on (via get_active_task_for_agent), never
    silently onto an older open one — which would clobber an already-submitted
    draft and report a false success."""
    agent_id = uuid4()
    older = _FakeTask(
        assigned_to=agent_id,
        draft={"occasion": "occ-a", "composition_id": "already-submitted"},
    )
    active = _FakeTask(assigned_to=agent_id, draft={"occasion": "occ-b"})
    task_svc = _mock_active_task(monkeypatch, active)

    env = await _actions("developer", "ux_ui").propose_video(
        agent_id=agent_id, **_valid_kwargs(composition_id="occ-b-composition")
    )

    assert env.error is None
    task_svc.get_active_task_for_agent.assert_awaited_once_with(agent_id)
    assert env.task_id == str(active.id)
    active_draft = markers.get_video_draft(active)
    assert active_draft is not None
    assert active_draft["composition_id"] == "occ-b-composition"
    older_draft = markers.get_video_draft(older)
    assert older_draft is not None
    assert older_draft["composition_id"] == "already-submitted"  # untouched


# --------------------------------------------------------------------------- #
# happy path — marker merge
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_video_merges_onto_active_task_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    authoring = _FakeTask(
        assigned_to=agent_id,
        draft={"occasion": "release v1.0.0", "script": "script", "brief": "brief"},
    )
    _mock_active_task(monkeypatch, authoring)

    env = await _actions("developer", "ux_ui").propose_video(
        agent_id=agent_id, **_valid_kwargs(input_props={"title": "Launch"})
    )

    assert env.error is None
    assert env.status == "video_proposed"
    assert env.task_id == str(authoring.id)
    draft = markers.get_video_draft(authoring)
    assert draft is not None
    # existing fields survive the merge
    assert draft["occasion"] == "release v1.0.0"
    assert draft["script"] == "script"
    assert draft["brief"] == "brief"
    # new fields land
    assert draft["composition_id"] == "release-announcement-v1"
    assert draft["input_props"] == {"title": "Launch"}
    assert draft["x_caption"] == _valid_kwargs()["x_caption"]
    assert draft["tiktok_caption"] == _valid_kwargs()["tiktok_caption"]
    assert draft["platforms"] == ["x", "tiktok"]


@pytest.mark.asyncio
async def test_propose_video_defaults_input_props_to_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    authoring = _FakeTask(assigned_to=agent_id)
    _mock_active_task(monkeypatch, authoring)

    env = await _actions("developer", "ux_ui").propose_video(
        agent_id=agent_id, **_valid_kwargs()
    )
    assert env.error is None
    draft = markers.get_video_draft(authoring)
    assert draft is not None
    assert draft["input_props"] == {}

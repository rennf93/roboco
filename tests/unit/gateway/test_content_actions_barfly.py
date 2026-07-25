"""roboco.services.gateway.content_actions.propose_conversation_replies —
HoM-gated Barfly conversation-reply authoring. Mirrors test_content_actions_
periscope.py's role-gate/validation shape and test_content_actions_scales.py's
candidate-must-be-real shape (there: task_ref against a live task; here:
tweet_id against the exploration task's own screened candidates)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps

TWO = 2


class _FakeTask:
    """Minimal stand-in for the ORM TaskTable row — carries just what
    ``propose_conversation_replies`` touches."""

    def __init__(
        self,
        *,
        assigned_to: Any,
        orchestration_markers: dict[str, Any] | None = None,
        status: Any = TaskStatus.PENDING,
        project_id: Any = None,
    ) -> None:
        self.id = uuid4()
        self.assigned_to = assigned_to
        self.orchestration_markers = orchestration_markers
        self.status = status
        self.project_id = project_id or uuid4()


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


def _candidate(tweet_id: str = "111") -> dict[str, Any]:
    return {
        "id": tweet_id,
        "author_handle": "someone",
        "text": "we should build a multi-agent coding org",
        "engagement_note": "3 combined likes/replies/retweets",
    }


def _cycle_task(
    *, agent_id: Any, candidates: list[dict[str, Any]] | None = None
) -> _FakeTask:
    task = _FakeTask(assigned_to=agent_id)
    markers.set_barfly_candidates(
        task, candidates if candidates is not None else [_candidate()]
    )
    return task


def _valid_item(**overrides: Any) -> dict[str, Any]:
    item = {
        "tweet_id": "111",
        "reply_body": "That's exactly what we built with the sandbox DB feature.",
        "rationale": "Directly relevant — a real answer to their question.",
    }
    item.update(overrides)
    return item


# --------------------------------------------------------------------------- #
# Role gate
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_conversation_replies_forbidden_for_product_owner() -> None:
    env = await _actions("product_owner").propose_conversation_replies(
        agent_id=uuid4(), items=[_valid_item()]
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_conversation_replies_forbidden_for_developer() -> None:
    env = await _actions("developer").propose_conversation_replies(
        agent_id=uuid4(), items=[_valid_item()]
    )
    assert env.error == "not_authorized"


# --------------------------------------------------------------------------- #
# Item-count bounds (1-5)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_conversation_replies_rejects_empty_items() -> None:
    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=uuid4(), items=[]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_conversation_replies_rejects_too_many_items() -> None:
    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=uuid4(), items=[_valid_item(tweet_id=str(i)) for i in range(6)]
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# Item shape validation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_conversation_replies_rejects_non_dict_item() -> None:
    bad_items: list[Any] = ["not a dict"]
    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=uuid4(), items=bad_items
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_conversation_replies_rejects_missing_tweet_id() -> None:
    bad = _valid_item()
    del bad["tweet_id"]
    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "tweet_id" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_conversation_replies_rejects_missing_reply_body() -> None:
    bad = _valid_item()
    del bad["reply_body"]
    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "reply_body" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_conversation_replies_rejects_soup_reply_body() -> None:
    bad = _valid_item(reply_body="asdf")
    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_conversation_replies_rejects_oversized_reply_body() -> None:
    bad = _valid_item(reply_body="x" * 281)
    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_conversation_replies_rejects_missing_rationale() -> None:
    bad = _valid_item()
    del bad["rationale"]
    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "rationale" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_conversation_replies_rejects_soup_rationale() -> None:
    bad = _valid_item(rationale="tbd")
    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_conversation_replies_rejects_oversized_rationale() -> None:
    bad = _valid_item(rationale="x" * 301)
    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# The load-bearing check: tweet_id must be a real candidate on THIS task
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_conversation_replies_rejects_unknown_tweet_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invented tweet_id — not on the exploration task's own screened
    candidate list — is rejected, naming the valid ids."""
    agent_id = uuid4()
    cycle_task = _cycle_task(agent_id=agent_id, candidates=[_candidate("111")])
    task_svc = MagicMock()
    task_svc.list_open_barfly_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)

    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=agent_id, items=[_valid_item(tweet_id="999-invented")]
    )
    assert env.error == "invalid_state"
    assert "does not match any candidate" in (env.message or "")
    assert "111" in (env.remediate or "")


# --------------------------------------------------------------------------- #
# Open-cycle lookup
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_conversation_replies_no_open_cycle_is_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_svc = MagicMock()
    task_svc.list_open_barfly_cycles = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=uuid4(), items=[_valid_item()]
    )
    assert env.error == "invalid_state"
    assert "no open barfly exploration" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_conversation_replies_ignores_cycle_assigned_to_another_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_agent = uuid4()
    cycle_task = _cycle_task(agent_id=other_agent)
    task_svc = MagicMock()
    task_svc.list_open_barfly_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("head_marketing").propose_conversation_replies(
        agent_id=uuid4(), items=[_valid_item()]
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# Happy path — complete-at-propose, multiplied across N materialized replies
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_conversation_replies_materializes_each_item_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _cycle_task(
        agent_id=agent_id,
        candidates=[_candidate("111"), _candidate("222")],
    )
    task_svc = MagicMock()
    task_svc.list_open_barfly_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)

    materialized_1 = _FakeTask(assigned_to=agent_id)
    materialized_2 = _FakeTask(assigned_to=agent_id)
    engine = MagicMock()
    engine.materialize_barfly_reply = AsyncMock(
        side_effect=[materialized_1, materialized_2]
    )
    monkeypatch.setattr("roboco.services.x_engine.get_x_engine", lambda _s: engine)

    actions = _actions("head_marketing")
    actions.task.session.flush = AsyncMock()

    env = await actions.propose_conversation_replies(
        agent_id=agent_id,
        items=[
            _valid_item(tweet_id="111", reply_body="Reply one, substantive text."),
            _valid_item(tweet_id="222", reply_body="Reply two, substantive text."),
        ],
    )
    assert env.error is None, env.message
    assert env.status == "conversation_replies_proposed"
    assert env.task_id == str(cycle_task.id)
    assert engine.materialize_barfly_reply.await_count == TWO
    assert cycle_task.status == TaskStatus.COMPLETED
    assert env.context_briefing["item_count"] == TWO
    assert set(env.context_briefing["materialized_task_ids"]) == {
        str(materialized_1.id),
        str(materialized_2.id),
    }


@pytest.mark.asyncio
async def test_propose_conversation_replies_passes_correct_candidate_to_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    candidate = _candidate("111")
    cycle_task = _cycle_task(agent_id=agent_id, candidates=[candidate])
    task_svc = MagicMock()
    task_svc.list_open_barfly_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)

    engine = MagicMock()
    engine.materialize_barfly_reply = AsyncMock(
        return_value=_FakeTask(assigned_to=agent_id)
    )
    monkeypatch.setattr("roboco.services.x_engine.get_x_engine", lambda _s: engine)

    actions = _actions("head_marketing")
    actions.task.session.flush = AsyncMock()

    env = await actions.propose_conversation_replies(
        agent_id=agent_id, items=[_valid_item(tweet_id="111")]
    )
    assert env.error is None, env.message
    call = engine.materialize_barfly_reply.await_args
    assert call.kwargs["exploration_task"] is cycle_task
    assert call.kwargs["candidate"] == candidate
    assert call.kwargs["reply_body"] == _valid_item()["reply_body"]
    assert call.kwargs["rationale"] == _valid_item()["rationale"]

"""roboco.services.gateway.content_actions.propose_roadmap — PO-gated themed
roadmap cycle authoring."""

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
    ``markers`` and ``propose_roadmap`` touch."""

    def __init__(
        self,
        *,
        assigned_to: Any,
        orchestration_markers: dict[str, Any] | None = None,
    ) -> None:
        self.id = uuid4()
        self.assigned_to = assigned_to
        self.orchestration_markers = orchestration_markers


def _actions(role: str, *, notification_delivery: Any = None) -> ContentActions:
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
        notification_delivery=notification_delivery,
    )
    return ContentActions(deps)


def _valid_item(idx: int) -> dict[str, Any]:
    return {
        "title": f"Item {idx}",
        "description": f"A substantive description of item {idx}",
        "acceptance_criteria": ["it does the thing", "it is tested"],
        "project_slug": "backend-svc",
        "team": "backend",
        "priority": 2,
        "rationale": f"Because it matters, reason {idx}",
    }


def _valid_items(n: int) -> list[dict[str, Any]]:
    return [_valid_item(i) for i in range(n)]


@pytest.mark.asyncio
async def test_propose_roadmap_forbidden_for_non_po() -> None:
    env = await _actions("head_marketing").propose_roadmap(
        agent_id=uuid4(), cycle_goal="Close onboarding friction", items=_valid_items(3)
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_roadmap_forbidden_for_developer() -> None:
    env = await _actions("developer").propose_roadmap(
        agent_id=uuid4(), cycle_goal="Close onboarding friction", items=_valid_items(3)
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_roadmap_rejects_too_few_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "roadmap_min_items_per_cycle", 3)
    monkeypatch.setattr(cfg, "roadmap_max_items_per_cycle", 7)
    env = await _actions("product_owner").propose_roadmap(
        agent_id=uuid4(), cycle_goal="Close onboarding friction", items=_valid_items(2)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_roadmap_rejects_too_many_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "roadmap_min_items_per_cycle", 3)
    monkeypatch.setattr(cfg, "roadmap_max_items_per_cycle", 7)
    env = await _actions("product_owner").propose_roadmap(
        agent_id=uuid4(), cycle_goal="Close onboarding friction", items=_valid_items(8)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_roadmap_rejects_missing_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "roadmap_min_items_per_cycle", 1)
    monkeypatch.setattr(cfg, "roadmap_max_items_per_cycle", 7)
    bad = _valid_item(0)
    del bad["rationale"]
    env = await _actions("product_owner").propose_roadmap(
        agent_id=uuid4(), cycle_goal="Close onboarding friction", items=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_roadmap_rejects_unknown_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "roadmap_min_items_per_cycle", 1)
    monkeypatch.setattr(cfg, "roadmap_max_items_per_cycle", 7)
    bad = _valid_item(0)
    bad["team"] = "board"  # not a cell team
    env = await _actions("product_owner").propose_roadmap(
        agent_id=uuid4(), cycle_goal="Close onboarding friction", items=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_roadmap_no_open_cycle_is_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "roadmap_min_items_per_cycle", 1)
    monkeypatch.setattr(cfg, "roadmap_max_items_per_cycle", 7)
    task_svc = MagicMock()
    task_svc.list_open_roadmap_cycles = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("product_owner").propose_roadmap(
        agent_id=uuid4(), cycle_goal="Close onboarding friction", items=_valid_items(3)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_roadmap_persists_cycle_onto_open_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "roadmap_min_items_per_cycle", 1)
    monkeypatch.setattr(cfg, "roadmap_max_items_per_cycle", 7)
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_roadmap_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    actions = _actions("product_owner")
    actions.task.session.flush = AsyncMock()

    items = _valid_items(3)
    env = await actions.propose_roadmap(
        agent_id=agent_id, cycle_goal="Close onboarding friction", items=items
    )
    assert env.error is None
    assert env.status == "roadmap_proposed"
    assert env.task_id == str(cycle_task.id)

    payload = markers.get_roadmap_cycle(cycle_task)
    assert payload is not None
    assert payload["goal"] == "Close onboarding friction"
    assert len(payload["items"]) == len(items)
    assert all(it["status"] == "proposed" for it in payload["items"])
    assert payload["items"][0]["id"] == "item-0"
    actions.task.session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_propose_roadmap_sends_telegram_push_per_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Roadmap items only become CEO-actionable once propose_roadmap lands
    (the engine's exploration-task origination has nothing to review yet),
    so the push DM fires once per item here, not from RoadmapEngine."""
    monkeypatch.setattr(cfg, "roadmap_min_items_per_cycle", 1)
    monkeypatch.setattr(cfg, "roadmap_max_items_per_cycle", 7)
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_roadmap_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    notify = AsyncMock()
    actions = _actions("product_owner", notification_delivery=notify)
    actions.task.session.flush = AsyncMock()

    items = _valid_items(3)
    env = await actions.propose_roadmap(
        agent_id=agent_id, cycle_goal="Close onboarding friction", items=items
    )
    assert env.error is None

    assert notify.notify_ceo_of_queue_item.await_count == len(items)
    id8 = str(cycle_task.id)[:8]
    for i, call in enumerate(notify.notify_ceo_of_queue_item.await_args_list):
        assert call.kwargs["kind"] == "roadmap"
        assert call.kwargs["id8"] == id8
        assert call.kwargs["extra"] == f"item-{i}"
        assert call.kwargs["title"] == f"Item {i}"


@pytest.mark.asyncio
async def test_propose_roadmap_survives_telegram_push_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Telegram send failure must never block propose_roadmap itself."""
    monkeypatch.setattr(cfg, "roadmap_min_items_per_cycle", 1)
    monkeypatch.setattr(cfg, "roadmap_max_items_per_cycle", 7)
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_roadmap_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    notify = MagicMock()
    notify.notify_ceo_of_queue_item = AsyncMock(side_effect=RuntimeError("boom"))
    actions = _actions("product_owner", notification_delivery=notify)
    actions.task.session.flush = AsyncMock()

    env = await actions.propose_roadmap(
        agent_id=agent_id, cycle_goal="Close onboarding friction", items=_valid_items(2)
    )

    assert env.error is None
    assert env.status == "roadmap_proposed"


@pytest.mark.asyncio
async def test_propose_roadmap_ignores_cycle_assigned_to_another_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "roadmap_min_items_per_cycle", 1)
    monkeypatch.setattr(cfg, "roadmap_max_items_per_cycle", 7)
    other_agent = uuid4()
    cycle_task = _FakeTask(assigned_to=other_agent)
    task_svc = MagicMock()
    task_svc.list_open_roadmap_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("product_owner").propose_roadmap(
        agent_id=uuid4(), cycle_goal="Close onboarding friction", items=_valid_items(3)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_roadmap_ignores_already_authored_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "roadmap_min_items_per_cycle", 1)
    monkeypatch.setattr(cfg, "roadmap_max_items_per_cycle", 7)
    agent_id = uuid4()
    authored_task = _FakeTask(
        assigned_to=agent_id,
        orchestration_markers={"roadmap_cycle": {"goal": "old", "items": []}},
    )
    task_svc = MagicMock()
    task_svc.list_open_roadmap_cycles = AsyncMock(return_value=[authored_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("product_owner").propose_roadmap(
        agent_id=agent_id, cycle_goal="A second cycle", items=_valid_items(3)
    )
    assert env.error == "invalid_state"

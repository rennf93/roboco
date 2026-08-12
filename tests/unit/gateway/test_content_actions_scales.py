"""roboco.services.gateway.content_actions.propose_rebalance — PO-gated
Scales portfolio-rebalance authoring. Mirrors
test_content_actions_pest_control.py, plus the task_ref resolution +
action/new_priority validation truth table this verb adds on top."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


class _FakeTask:
    """Minimal stand-in for the ORM TaskTable row — carries just what
    ``markers`` and ``propose_rebalance`` touch."""

    def __init__(
        self,
        *,
        assigned_to: Any,
        orchestration_markers: dict[str, Any] | None = None,
    ) -> None:
        self.id = uuid4()
        self.assigned_to = assigned_to
        self.orchestration_markers = orchestration_markers


class _FakeTargetTask:
    """Minimal stand-in for a live BACKLOG/PENDING task a rebalance item
    targets — carries just what ``resolve_scales_task_ref`` returns."""

    def __init__(self, *, title: str = "Stale onboarding task") -> None:
        self.id = uuid4()
        self.title = title


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
        "task_ref": f"target-{idx}",
        "action": "reprioritize",
        "new_priority": 0,
        "rationale": f"A substantive rationale for item {idx} — top charter goal",
    }


def _valid_items(n: int) -> list[dict[str, Any]]:
    return [_valid_item(i) for i in range(n)]


@pytest.fixture(autouse=True)
def _default_task_ref_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every propose_rebalance call resolves each item's ``task_ref`` via
    ``TaskService.resolve_scales_task_ref``, then (once every item validates)
    looks up the caller's open cycle via ``list_open_scales_cycles``. Default
    both to "no cycle assigned" so tests that aren't exercising the
    resolution/cycle-lookup gates themselves don't each need to stub them —
    they land cleanly on the "no open cycle" invalid_state instead of
    crashing on an unconfigured MagicMock coroutine. Tests exercising either
    gate override this."""
    stub = MagicMock()
    stub.resolve_scales_task_ref = AsyncMock(side_effect=lambda _ref: _FakeTargetTask())
    stub.list_open_scales_cycles = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: stub)


@pytest.mark.asyncio
async def test_propose_rebalance_forbidden_for_non_po() -> None:
    env = await _actions("head_marketing").propose_rebalance(
        agent_id=uuid4(), items=_valid_items(2)
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_rebalance_forbidden_for_developer() -> None:
    env = await _actions("developer").propose_rebalance(
        agent_id=uuid4(), items=_valid_items(2)
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_rebalance_rejects_empty_items() -> None:
    env = await _actions("product_owner").propose_rebalance(agent_id=uuid4(), items=[])
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_rebalance_rejects_too_many_items() -> None:
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=_valid_items(8)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_rebalance_rejects_missing_task_ref() -> None:
    bad = _valid_item(0)
    del bad["task_ref"]
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "task_ref" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_rebalance_rejects_invalid_action() -> None:
    bad = _valid_item(0)
    bad["action"] = "delete"
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "action" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_rebalance_rejects_reprioritize_without_new_priority() -> None:
    bad = _valid_item(0)
    del bad["new_priority"]
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "new_priority" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_rebalance_rejects_out_of_range_priority() -> None:
    bad = _valid_item(0)
    bad["new_priority"] = 4
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_rebalance_rejects_bool_as_priority() -> None:
    """A bool is a Python int subclass — must not sneak through as 0/1."""
    bad = _valid_item(0)
    bad["new_priority"] = True
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_rebalance_allows_cancel_without_new_priority() -> None:
    item = {
        "task_ref": "target-0",
        "action": "cancel",
        "rationale": "No longer serves the charter, superseded by the new plan",
    }
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=[item]
    )
    # No open cycle assigned in this test — refused downstream, not by the
    # action/new_priority shape gate.
    assert env.error == "invalid_state"
    assert "no open scales exploration task" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_rebalance_rejects_missing_rationale() -> None:
    bad = _valid_item(0)
    del bad["rationale"]
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "rationale" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_rebalance_rejects_too_short_rationale() -> None:
    bad = _valid_item(0)
    bad["rationale"] = "short"  # 5 chars, under the 8-char floor
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "item 0" in (env.message or "")
    assert "rationale" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_rebalance_rejects_placeholder_soup_rationale() -> None:
    bad = _valid_item(0)
    bad["rationale"] = "tbd tbd tbd tbd tbd tbd"  # 23 chars, all filler tokens
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "rationale" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_rebalance_rejects_oversized_rationale() -> None:
    bad = _valid_item(0)
    bad["rationale"] = "x" * 501
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_rebalance_rejects_unresolvable_task_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = MagicMock()
    stub.resolve_scales_task_ref = AsyncMock(return_value=None)
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: stub)

    bad = _valid_item(0)
    bad["task_ref"] = "no-such-task"
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "no-such-task" in (env.message or "")
    assert "does not resolve" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_rebalance_no_open_cycle_is_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_svc = MagicMock()
    task_svc.resolve_scales_task_ref = AsyncMock(
        side_effect=lambda _ref: _FakeTargetTask()
    )
    task_svc.list_open_scales_cycles = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=_valid_items(2)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_rebalance_persists_plan_onto_open_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    target = _FakeTargetTask(title="Onboarding polish")
    task_svc = MagicMock()
    task_svc.resolve_scales_task_ref = AsyncMock(return_value=target)
    task_svc.list_open_scales_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    actions = _actions("product_owner")
    actions.task.session.flush = AsyncMock()

    items = _valid_items(3)
    env = await actions.propose_rebalance(agent_id=agent_id, items=items)
    assert env.error is None
    assert env.status == "rebalance_proposed"
    assert env.task_id == str(cycle_task.id)

    payload = markers.get_rebalance_plan(cycle_task)
    assert payload is not None
    assert len(payload["items"]) == len(items)
    assert all(it["status"] == "proposed" for it in payload["items"])
    assert payload["items"][0]["id"] == "item-0"
    assert payload["items"][0]["target_task_id"] == str(target.id)
    assert payload["items"][0]["target_task_title"] == "Onboarding polish"
    assert payload["items"][0]["new_priority"] == 0
    actions.task.session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_propose_rebalance_cancel_item_persists_null_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    target = _FakeTargetTask()
    task_svc = MagicMock()
    task_svc.resolve_scales_task_ref = AsyncMock(return_value=target)
    task_svc.list_open_scales_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    actions = _actions("product_owner")
    actions.task.session.flush = AsyncMock()

    item = {
        "task_ref": "target-0",
        "action": "cancel",
        "new_priority": 3,  # provided but irrelevant — must be dropped
        "rationale": "No longer serves the charter, superseded by the new plan",
    }
    env = await actions.propose_rebalance(agent_id=agent_id, items=[item])
    assert env.error is None

    payload = markers.get_rebalance_plan(cycle_task)
    assert payload is not None
    assert payload["items"][0]["action"] == "cancel"
    assert payload["items"][0]["new_priority"] is None


@pytest.mark.asyncio
async def test_propose_rebalance_sends_telegram_push_per_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    target = _FakeTargetTask(title="Onboarding polish")
    task_svc = MagicMock()
    task_svc.resolve_scales_task_ref = AsyncMock(return_value=target)
    task_svc.list_open_scales_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    notify = AsyncMock()
    actions = _actions("product_owner", notification_delivery=notify)
    actions.task.session.flush = AsyncMock()

    items = _valid_items(2)
    env = await actions.propose_rebalance(agent_id=agent_id, items=items)
    assert env.error is None

    assert notify.notify_ceo_of_queue_item.await_count == len(items)
    id8 = str(cycle_task.id)[:8]
    for i, call in enumerate(notify.notify_ceo_of_queue_item.await_args_list):
        assert call.kwargs["kind"] == "scales"
        assert call.kwargs["id8"] == id8
        assert call.kwargs["extra"] == f"item-{i}"
        assert call.kwargs["title"] == "Onboarding polish"
        # DEFECT 2 regression: without related_task_id the row can never
        # auto-resolve once every item on the cycle is decided.
        assert call.kwargs["related_task_id"] == cycle_task.id


@pytest.mark.asyncio
async def test_propose_rebalance_survives_telegram_push_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    target = _FakeTargetTask()
    task_svc = MagicMock()
    task_svc.resolve_scales_task_ref = AsyncMock(return_value=target)
    task_svc.list_open_scales_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    notify = MagicMock()
    notify.notify_ceo_of_queue_item = AsyncMock(side_effect=RuntimeError("boom"))
    actions = _actions("product_owner", notification_delivery=notify)
    actions.task.session.flush = AsyncMock()

    env = await actions.propose_rebalance(agent_id=agent_id, items=_valid_items(1))

    assert env.error is None
    assert env.status == "rebalance_proposed"


@pytest.mark.asyncio
async def test_propose_rebalance_ignores_cycle_assigned_to_another_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_agent = uuid4()
    cycle_task = _FakeTask(assigned_to=other_agent)
    task_svc = MagicMock()
    task_svc.resolve_scales_task_ref = AsyncMock(
        side_effect=lambda _ref: _FakeTargetTask()
    )
    task_svc.list_open_scales_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("product_owner").propose_rebalance(
        agent_id=uuid4(), items=_valid_items(2)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_rebalance_ignores_already_authored_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    authored_task = _FakeTask(
        assigned_to=agent_id,
        orchestration_markers={"rebalance_plan": {"items": []}},
    )
    task_svc = MagicMock()
    task_svc.resolve_scales_task_ref = AsyncMock(
        side_effect=lambda _ref: _FakeTargetTask()
    )
    task_svc.list_open_scales_cycles = AsyncMock(return_value=[authored_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("product_owner").propose_rebalance(
        agent_id=agent_id, items=_valid_items(2)
    )
    assert env.error == "invalid_state"

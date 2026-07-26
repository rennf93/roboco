"""roboco.services.gateway.content_actions.propose_quality_report —
Auditor-gated Sentinel quality-report authoring. Mirrors
test_content_actions_periscope.py for the validation truth table and the
complete-at-propose asymmetry (a report has no per-item CEO queue); adds the
area-vocabulary check propose_market_brief has no equivalent of."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


class _FakeTask:
    """Minimal stand-in for the ORM TaskTable row — carries just what
    ``propose_quality_report`` touches."""

    def __init__(
        self,
        *,
        assigned_to: Any,
        orchestration_markers: dict[str, Any] | None = None,
        status: Any = TaskStatus.PENDING,
    ) -> None:
        self.id = uuid4()
        self.assigned_to = assigned_to
        self.orchestration_markers = orchestration_markers
        self.status = status


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


def _valid_item(idx: int, *, area: str = "waivers") -> dict[str, Any]:
    return {
        "area": area,
        "observation": f"Observation {idx} about recurring drift",
        "evidence": f"Ledger row {idx} backing this observation",
        "suggested_action": f"Suggested action {idx} for the CEO",
    }


def _valid_items(n: int) -> list[dict[str, Any]]:
    return [_valid_item(i) for i in range(n)]


def _valid_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "headline": "Waived findings climbed sharply this week",
        "items": _valid_items(1),
        "overall_assessment": "Drift is concentrated in one hotspot, not systemic",
    }
    kwargs.update(overrides)
    return kwargs


# --------------------------------------------------------------------------- #
# Role gate
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_quality_report_forbidden_for_product_owner() -> None:
    env = await _actions("product_owner").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_quality_report_forbidden_for_head_marketing() -> None:
    env = await _actions("head_marketing").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_quality_report_forbidden_for_developer() -> None:
    env = await _actions("developer").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "not_authorized"


# --------------------------------------------------------------------------- #
# Headline validation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_short_headline() -> None:
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(headline="short")
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_oversized_headline() -> None:
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(headline="x" * 201)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_soup_headline() -> None:
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(headline="tbd tbd tbd")
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# Items count + shape
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_empty_items() -> None:
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=[])
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_too_many_items() -> None:
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=_valid_items(8))
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_non_dict_item() -> None:
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=["not a dict"])
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_item_missing_observation() -> None:
    bad = _valid_item(0)
    del bad["observation"]
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=[bad])
    )
    assert env.error == "invalid_state"
    assert "observation" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_item_missing_evidence() -> None:
    bad = _valid_item(0)
    del bad["evidence"]
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=[bad])
    )
    assert env.error == "invalid_state"
    assert "evidence" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_item_missing_suggested_action() -> None:
    bad = _valid_item(0)
    del bad["suggested_action"]
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=[bad])
    )
    assert env.error == "invalid_state"
    assert "suggested_action" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_soup_observation() -> None:
    bad = _valid_item(0)
    bad["observation"] = "asdf"
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=[bad])
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_oversized_observation() -> None:
    bad = _valid_item(0)
    bad["observation"] = "x" * 501
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=[bad])
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_oversized_evidence() -> None:
    bad = _valid_item(0)
    bad["evidence"] = "x" * 501
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=[bad])
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_oversized_suggested_action() -> None:
    bad = _valid_item(0)
    bad["suggested_action"] = "x" * 301
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=[bad])
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# Area vocabulary — the check propose_market_brief has no equivalent of
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_unknown_area() -> None:
    bad = _valid_item(0, area="not_a_real_area")
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=[bad])
    )
    assert env.error == "invalid_state"
    assert "area" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_missing_area() -> None:
    bad = _valid_item(0)
    del bad["area"]
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=[bad])
    )
    assert env.error == "invalid_state"


@pytest.mark.parametrize(
    "area", ["waivers", "findings", "conventions", "budget", "docs", "other"]
)
@pytest.mark.asyncio
async def test_propose_quality_report_accepts_every_valid_area(
    area: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_svc = MagicMock()
    task_svc.list_open_sentinel_cycles = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(items=[_valid_item(0, area=area)])
    )
    # No open cycle exists — this is the FIRST check past field validation,
    # proving the area itself was accepted.
    assert env.error == "invalid_state"
    assert "area" not in (env.message or "")
    assert "no open sentinel exploration" in (env.message or "")


# --------------------------------------------------------------------------- #
# overall_assessment
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_missing_overall_assessment() -> None:
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(overall_assessment="")
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_oversized_overall_assessment() -> None:
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(overall_assessment="x" * 801)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_quality_report_rejects_soup_overall_assessment() -> None:
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs(overall_assessment="tbd tbd tbd")
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# Open-cycle lookup
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_quality_report_no_open_cycle_is_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_svc = MagicMock()
    task_svc.list_open_sentinel_cycles = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_quality_report_ignores_cycle_assigned_to_another_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_agent = uuid4()
    cycle_task = _FakeTask(assigned_to=other_agent)
    task_svc = MagicMock()
    task_svc.list_open_sentinel_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(), **_valid_kwargs()
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_quality_report_ignores_already_authored_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    authored = _FakeTask(
        assigned_to=agent_id,
        orchestration_markers={"quality_report": {"headline": "already filed"}},
    )
    task_svc = MagicMock()
    task_svc.list_open_sentinel_cycles = AsyncMock(return_value=[authored])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("auditor").propose_quality_report(
        agent_id=agent_id, **_valid_kwargs()
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# Happy path — the complete-at-propose transition
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_quality_report_persists_and_completes_the_exploration_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_sentinel_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    actions = _actions("auditor")
    actions.task.session.flush = AsyncMock()

    items = _valid_items(3)
    env = await actions.propose_quality_report(
        agent_id=agent_id,
        headline="Waived findings climbed sharply this week",
        items=items,
        overall_assessment="Drift is concentrated in one hotspot, not systemic",
    )
    assert env.error is None
    assert env.status == "quality_report_proposed"
    assert env.task_id == str(cycle_task.id)

    payload = markers.get_quality_report(cycle_task)
    assert payload is not None
    assert payload["headline"] == "Waived findings climbed sharply this week"
    assert len(payload["items"]) == len(items)
    assert payload["items"][0]["id"] == "item-0"
    assert payload["items"][0]["area"] == "waivers"
    # Each item still carries its own per-item CEO decision (SentinelService.
    # approve_item/reject_item) even though the exploration task completes
    # here.
    assert payload["items"][0]["status"] == "proposed"
    assert payload["items"][0]["materialized_task_id"] is None
    assert (
        payload["overall_assessment"]
        == "Drift is concentrated in one hotspot, not systemic"
    )

    # The x_feature/periscope asymmetry: the exploration task completes in
    # THIS call — a report has no per-item CEO decision to wait on.
    assert cycle_task.status == TaskStatus.COMPLETED
    actions.task.session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_propose_quality_report_sends_telegram_push_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ONE call per cycle — a report, not N per-item queue items (unlike
    propose_bug_hunt/propose_roadmap's per-item Telegram push)."""
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_sentinel_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    notify = AsyncMock()
    actions = _actions("auditor", notification_delivery=notify)
    actions.task.session.flush = AsyncMock()

    env = await actions.propose_quality_report(agent_id=agent_id, **_valid_kwargs())
    assert env.error is None

    notify.notify_ceo_of_sentinel_report.assert_awaited_once()
    call = notify.notify_ceo_of_sentinel_report.await_args
    assert call.kwargs["task"] is cycle_task
    assert call.kwargs["task_id"] == cycle_task.id
    assert call.kwargs["headline"] == "Waived findings climbed sharply this week"


@pytest.mark.asyncio
async def test_propose_quality_report_survives_telegram_push_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_sentinel_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    notify = MagicMock()
    notify.notify_ceo_of_sentinel_report = AsyncMock(side_effect=RuntimeError("boom"))
    actions = _actions("auditor", notification_delivery=notify)
    actions.task.session.flush = AsyncMock()

    env = await actions.propose_quality_report(agent_id=agent_id, **_valid_kwargs())

    assert env.error is None
    assert env.status == "quality_report_proposed"


@pytest.mark.asyncio
async def test_propose_quality_report_no_notification_delivery_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_sentinel_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    actions = _actions("auditor", notification_delivery=None)
    actions.task.session.flush = AsyncMock()

    env = await actions.propose_quality_report(agent_id=agent_id, **_valid_kwargs())
    assert env.error is None


@pytest.mark.asyncio
async def test_propose_quality_report_missing_items_is_invalid_state() -> None:
    env = await _actions("auditor").propose_quality_report(
        agent_id=uuid4(),
        headline="A one-line summary of this cycle's biggest signal",
        items=[],
        overall_assessment="An overall assessment of substantive length",
    )
    assert env.error == "invalid_state"

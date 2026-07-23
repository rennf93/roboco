"""Task-level $ budget sweep (ROBOCO_TASK_BUDGETS_ENABLED).

`_sweep_budget_exceeded` gains a second trigger alongside the existing
tool-call halt: when the flag is on and an active task's own $ budget is
breached, the task is BLOCKED + the CEO notified (`_handle_task_budget_breach`)
BEFORE the agent is gracefully stopped — never a mid-verb kill, and never left
to bounce through `pending` for an instant re-claim.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.models.base import BlockerResolverType, TaskStatus, TaskType
from roboco.runtime.orchestrator import AgentOrchestrator, AgentState

_MOCK_TASK_SPEND_USD = 3.0


def _make_orchestrator() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._lock = MagicMock()
    return orch


def _instance(task_id: str | None) -> MagicMock:
    inst = MagicMock()
    inst.state = AgentState.ACTIVE
    inst.container_id = "deadbeef1234"
    inst.current_task_id = task_id
    inst.error_count = 0
    inst.config = MagicMock(git_context=None)
    return inst


def _db_ctx(db: Any) -> Any:
    @asynccontextmanager
    async def _ctx() -> Any:
        yield db

    return _ctx


@pytest.mark.asyncio
async def test_task_budget_breach_blocks_before_graceful_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _make_orchestrator()
    task_id = "11111111-1111-1111-1111-111111111111"
    orch._instances = {"be-dev-1": _instance(task_id)}
    monkeypatch.setattr(settings, "task_budgets_enabled", True)

    with (
        patch.object(
            AgentOrchestrator, "_fetch_budget_status", AsyncMock(return_value=None)
        ),
        patch.object(orch, "_task_budget_breach", AsyncMock(return_value=(5.0, 7.5))),
        patch.object(orch, "_handle_task_budget_breach", AsyncMock()) as handle_mock,
        patch.object(orch, "stop_agent", AsyncMock()) as stop_mock,
    ):
        await orch._sweep_budget_exceeded()

    # Block + notify runs, and runs BEFORE stop_agent (never a mid-verb kill —
    # graceful=True, and the task is already blocked by the time the agent dies).
    handle_mock.assert_awaited_once_with(task_id, cap_usd=5.0, spend_usd=7.5)
    stop_mock.assert_awaited_once_with(
        "be-dev-1",
        graceful=True,
        release_claim=True,
        stop_reason="budget_exceeded_task",
    )


@pytest.mark.asyncio
async def test_flag_off_never_checks_task_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _make_orchestrator()
    task_id = "11111111-1111-1111-1111-111111111111"
    orch._instances = {"be-dev-1": _instance(task_id)}
    monkeypatch.setattr(settings, "task_budgets_enabled", False)

    with (
        patch.object(
            AgentOrchestrator, "_fetch_budget_status", AsyncMock(return_value=None)
        ),
        patch.object(orch, "_task_budget_breach", AsyncMock()) as breach_mock,
        patch.object(orch, "stop_agent", AsyncMock()) as stop_mock,
    ):
        await orch._sweep_budget_exceeded()

    breach_mock.assert_not_awaited()
    stop_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_under_budget_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = _make_orchestrator()
    task_id = "11111111-1111-1111-1111-111111111111"
    orch._instances = {"be-dev-1": _instance(task_id)}
    monkeypatch.setattr(settings, "task_budgets_enabled", True)

    with (
        patch.object(
            AgentOrchestrator, "_fetch_budget_status", AsyncMock(return_value=None)
        ),
        patch.object(orch, "_task_budget_breach", AsyncMock(return_value=None)),
        patch.object(orch, "_handle_task_budget_breach", AsyncMock()) as handle_mock,
        patch.object(orch, "stop_agent", AsyncMock()) as stop_mock,
    ):
        await orch._sweep_budget_exceeded()

    handle_mock.assert_not_awaited()
    stop_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_call_halt_path_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-existing tool-call halt trigger still fires with its own
    stop_reason, independent of the new $ budget path."""
    orch = _make_orchestrator()
    orch._instances = {"be-dev-1": _instance(None)}
    monkeypatch.setattr(settings, "task_budgets_enabled", False)

    halt_status = {"halt": True, "total": 301, "halt_threshold": 300}
    with (
        patch.object(
            AgentOrchestrator,
            "_fetch_budget_status",
            AsyncMock(return_value=halt_status),
        ),
        patch.object(orch, "stop_agent", AsyncMock()) as stop_mock,
    ):
        await orch._sweep_budget_exceeded()

    stop_mock.assert_awaited_once_with(
        "be-dev-1", graceful=True, release_claim=True, stop_reason="budget_sweep"
    )


@pytest.mark.asyncio
async def test_no_task_id_skips_task_budget_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A taskless spawn (current_task_id=None) never reaches the $ budget
    check even with the flag on."""
    orch = _make_orchestrator()
    orch._instances = {"be-dev-1": _instance(None)}
    monkeypatch.setattr(settings, "task_budgets_enabled", True)

    with (
        patch.object(
            AgentOrchestrator, "_fetch_budget_status", AsyncMock(return_value=None)
        ),
        patch.object(orch, "_task_budget_breach", AsyncMock()) as breach_mock,
        patch.object(orch, "stop_agent", AsyncMock()) as stop_mock,
    ):
        await orch._sweep_budget_exceeded()

    breach_mock.assert_not_awaited()
    stop_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# _handle_task_budget_breach: the block + notify write itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_breach_blocks_task_and_notifies_ceo() -> None:
    orch = _make_orchestrator()
    task_id = "22222222-2222-2222-2222-222222222222"
    task = MagicMock(status=TaskStatus.IN_PROGRESS)
    db = MagicMock()

    task_svc = MagicMock()
    task_svc.get = AsyncMock(return_value=task)
    task_svc.admin_set_status = AsyncMock()
    delivery = MagicMock()
    delivery.notify_ceo_of_budget_breach = AsyncMock()

    with (
        patch("roboco.db.base.get_db_context", _db_ctx(db)),
        patch("roboco.services.task.TaskService", return_value=task_svc),
        patch(
            "roboco.services.notification_delivery.get_notification_delivery_service",
            return_value=delivery,
        ),
    ):
        await orch._handle_task_budget_breach(task_id, cap_usd=5.0, spend_usd=8.0)

    assert task.blocker_resolver_type == BlockerResolverType.HUMAN
    task_svc.admin_set_status.assert_awaited_once()
    args, _kwargs = task_svc.admin_set_status.call_args
    assert args[1] == TaskStatus.BLOCKED
    delivery.notify_ceo_of_budget_breach.assert_awaited_once_with(
        task=task, task_id=args[0], cap_usd=5.0, spend_usd=8.0
    )


@pytest.mark.asyncio
async def test_handle_breach_skips_a_task_that_already_moved_on() -> None:
    """A stale re-check racing the task's own progress (e.g. it completed
    between the read and the write) must not block/notify."""
    orch = _make_orchestrator()
    task_id = "33333333-3333-3333-3333-333333333333"
    task = MagicMock(status=TaskStatus.COMPLETED)
    db = MagicMock()

    task_svc = MagicMock()
    task_svc.get = AsyncMock(return_value=task)
    task_svc.admin_set_status = AsyncMock()
    delivery = MagicMock()
    delivery.notify_ceo_of_budget_breach = AsyncMock()

    with (
        patch("roboco.db.base.get_db_context", _db_ctx(db)),
        patch("roboco.services.task.TaskService", return_value=task_svc),
        patch(
            "roboco.services.notification_delivery.get_notification_delivery_service",
            return_value=delivery,
        ),
    ):
        await orch._handle_task_budget_breach(task_id, cap_usd=5.0, spend_usd=8.0)

    task_svc.admin_set_status.assert_not_awaited()
    delivery.notify_ceo_of_budget_breach.assert_not_awaited()


# ---------------------------------------------------------------------------
# _task_budget_breach: explicit-input-only cap (null budget = never a breach)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_budget_is_never_a_breach() -> None:
    """Budgets enforce only when explicitly set: a task with no budget_usd is
    uncapped, regardless of spend — the spend query is never even issued (the
    old per-TaskType default table blocked a default-budget coordination root
    one opus planning turn in)."""
    orch = _make_orchestrator()
    task_id = "44444444-4444-4444-4444-444444444444"
    task = MagicMock(
        status=TaskStatus.IN_PROGRESS, task_type=TaskType.DOCUMENTATION, budget_usd=None
    )
    task_svc = MagicMock()
    task_svc.get = AsyncMock(return_value=task)
    task_svc.task_spend_usd = AsyncMock(return_value=_MOCK_TASK_SPEND_USD)
    db = MagicMock()

    with (
        patch("roboco.db.base.get_db_context", _db_ctx(db)),
        patch("roboco.services.task.TaskService", return_value=task_svc),
    ):
        breach = await orch._task_budget_breach(task_id)

    assert breach is None
    task_svc.task_spend_usd.assert_not_awaited()


@pytest.mark.asyncio
async def test_breach_none_when_task_left_claimed_or_in_progress() -> None:
    """A stale re-check (the task already reached e.g. awaiting_qa) is not a
    breach — the spend query is never even issued."""
    orch = _make_orchestrator()
    task_id = "55555555-5555-5555-5555-555555555555"
    task = MagicMock(
        status=TaskStatus.AWAITING_QA, task_type=TaskType.CODE, budget_usd=1.0
    )
    task_svc = MagicMock()
    task_svc.get = AsyncMock(return_value=task)
    task_svc.task_spend_usd = AsyncMock()
    db = MagicMock()

    with (
        patch("roboco.db.base.get_db_context", _db_ctx(db)),
        patch("roboco.services.task.TaskService", return_value=task_svc),
    ):
        breach = await orch._task_budget_breach(task_id)

    assert breach is None
    task_svc.task_spend_usd.assert_not_awaited()


# ---------------------------------------------------------------------------
# Repeated ticks: a blocked task must not re-fire the breach handling.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_ticks_do_not_refire_once_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two consecutive _sweep_budget_exceeded ticks against the same still-
    registered instance: tick 1 detects the breach and blocks the task; by
    tick 2 the task is BLOCKED (no longer CLAIMED/IN_PROGRESS), so
    _task_budget_breach's own status guard returns None — the sweep never
    re-blocks / re-notifies / re-stops a task that's already been handled."""
    orch = _make_orchestrator()
    task_id = "77777777-7777-7777-7777-777777777777"
    orch._instances = {"be-dev-1": _instance(task_id)}
    monkeypatch.setattr(settings, "task_budgets_enabled", True)

    in_progress_task = MagicMock(
        status=TaskStatus.IN_PROGRESS, task_type=TaskType.CODE, budget_usd=5.0
    )
    # Simulates the task having been transitioned to BLOCKED by tick 1's
    # (mocked-out) _handle_task_budget_breach before tick 2 re-checks it.
    blocked_task = MagicMock(
        status=TaskStatus.BLOCKED, task_type=TaskType.CODE, budget_usd=5.0
    )
    task_svc = MagicMock()
    task_svc.get = AsyncMock(side_effect=[in_progress_task, blocked_task])
    task_svc.task_spend_usd = AsyncMock(return_value=7.0)
    db = MagicMock()

    with (
        patch.object(
            AgentOrchestrator, "_fetch_budget_status", AsyncMock(return_value=None)
        ),
        patch("roboco.db.base.get_db_context", _db_ctx(db)),
        patch("roboco.services.task.TaskService", return_value=task_svc),
        patch.object(orch, "_handle_task_budget_breach", AsyncMock()) as handle_mock,
        patch.object(orch, "stop_agent", AsyncMock()) as stop_mock,
    ):
        await orch._sweep_budget_exceeded()  # tick 1: breach detected
        await orch._sweep_budget_exceeded()  # tick 2: already blocked

    handle_mock.assert_awaited_once()
    stop_mock.assert_awaited_once()

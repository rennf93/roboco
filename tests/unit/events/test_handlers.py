"""Event handler coverage — fanout to notification service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import Iterator
from uuid import uuid4

import pytest
from roboco.events.bus import Event, EventType
from roboco.events.handlers import (
    _get_doc_id,
    _get_pm_id,
    _get_qa_id,
    get_event_context,
    handle_blocker_resolved,
    handle_handoff_created,
    handle_qa_result,
    handle_question_answered,
    handle_task_status_change,
    register_default_handlers,
    set_event_context,
)


def _make_event(event_type: EventType, **data: Any) -> Event:
    return Event(
        type=event_type,
        data=data,
        source_agent="be-dev-1",
    )


@pytest.fixture(autouse=True)
def reset_context() -> Iterator[None]:
    """Reset event context after each test.

    set_event_context only updates attrs when truthy, so we need to
    directly clear the underlying singleton EventContext to avoid
    leaking state between tests.
    """
    yield
    ctx = get_event_context()
    ctx.notification_service = None
    ctx.orchestrator = None


# ---------------------------------------------------------------------------
# ID builders
# ---------------------------------------------------------------------------


def test_get_pm_id() -> None:
    assert _get_pm_id("backend") == "ba-pm"


def test_get_qa_id() -> None:
    assert _get_qa_id("frontend") == "fr-qa"


def test_get_doc_id() -> None:
    assert _get_doc_id("backend") == "ba-doc"


# ---------------------------------------------------------------------------
# Task status handlers — no-op when no notification service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_task_blocked_without_context_is_noop() -> None:
    event = _make_event(
        EventType.TASK_BLOCKED, task_id=str(uuid4()), team="backend", reason="x"
    )
    # No notification_service set — does nothing.
    await handle_task_status_change(event)


@pytest.mark.asyncio
async def test_handle_task_blocked_calls_send_blocker() -> None:
    notif = MagicMock()
    notif.send_blocker_notification = AsyncMock()
    set_event_context(notification_service=notif)
    event = _make_event(
        EventType.TASK_BLOCKED,
        task_id=str(uuid4()),
        team="backend",
        reason="x",
        task_title="Ship the widget",
    )
    await handle_task_status_change(event)
    notif.send_blocker_notification.assert_called_once()
    assert (
        notif.send_blocker_notification.call_args.kwargs["task_title"]
        == "Ship the widget"
    )


@pytest.mark.asyncio
async def test_handle_task_blocked_skips_when_no_team() -> None:
    notif = MagicMock()
    notif.send_blocker_notification = AsyncMock()
    set_event_context(notification_service=notif)
    event = _make_event(EventType.TASK_BLOCKED, task_id=str(uuid4()))
    await handle_task_status_change(event)
    notif.send_blocker_notification.assert_not_called()


@pytest.mark.asyncio
async def test_handle_task_awaiting_qa() -> None:
    notif = MagicMock()
    notif.send_qa_ready_notification = AsyncMock()
    set_event_context(notification_service=notif)
    event = _make_event(
        EventType.TASK_AWAITING_QA,
        task_id=str(uuid4()),
        team="backend",
        task_title="Ship the widget",
    )
    await handle_task_status_change(event)
    notif.send_qa_ready_notification.assert_called_once()
    assert (
        notif.send_qa_ready_notification.call_args.kwargs["task_title"]
        == "Ship the widget"
    )


@pytest.mark.asyncio
async def test_handle_task_qa_failed() -> None:
    notif = MagicMock()
    notif.send_qa_failed_notification = AsyncMock()
    set_event_context(notification_service=notif)
    event = _make_event(
        EventType.TASK_QA_FAILED,
        task_id=str(uuid4()),
        assigned_to="be-dev-1",
        qa_notes="please fix",
        task_title="Ship the widget",
    )
    await handle_task_status_change(event)
    notif.send_qa_failed_notification.assert_called_once()
    assert (
        notif.send_qa_failed_notification.call_args.kwargs["task_title"]
        == "Ship the widget"
    )


@pytest.mark.asyncio
async def test_handle_task_qa_failed_no_assigned_to_skips() -> None:
    notif = MagicMock()
    notif.send_qa_failed_notification = AsyncMock()
    set_event_context(notification_service=notif)
    event = _make_event(EventType.TASK_QA_FAILED, task_id=str(uuid4()))
    await handle_task_status_change(event)
    notif.send_qa_failed_notification.assert_not_called()


@pytest.mark.asyncio
async def test_handle_task_awaiting_docs() -> None:
    notif = MagicMock()
    notif.send_docs_ready_notification = AsyncMock()
    set_event_context(notification_service=notif)
    event = _make_event(
        EventType.TASK_AWAITING_DOCS,
        task_id=str(uuid4()),
        team="backend",
        task_title="Ship the widget",
    )
    await handle_task_status_change(event)
    notif.send_docs_ready_notification.assert_called_once()
    assert (
        notif.send_docs_ready_notification.call_args.kwargs["task_title"]
        == "Ship the widget"
    )


@pytest.mark.asyncio
async def test_handle_task_status_change_unknown_type_noop() -> None:
    """No mapping for TASK_CREATED — does nothing."""
    event = _make_event(EventType.TASK_CREATED, task_id=str(uuid4()))
    await handle_task_status_change(event)  # No raise.


# ---------------------------------------------------------------------------
# Handoff handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_handoff_created_calls_notification() -> None:
    notif = MagicMock()
    notif.send_handoff_notification = AsyncMock()
    set_event_context(notification_service=notif)
    event = _make_event(
        EventType.HANDOFF_CREATED,
        task_id=str(uuid4()),
        handoff_id=str(uuid4()),
        team="backend",
        task_title="Ship the widget",
    )
    await handle_handoff_created(event)
    notif.send_handoff_notification.assert_called_once()
    assert (
        notif.send_handoff_notification.call_args.kwargs["task_title"]
        == "Ship the widget"
    )


@pytest.mark.asyncio
async def test_handle_handoff_created_no_team_skips_notification() -> None:
    notif = MagicMock()
    notif.send_handoff_notification = AsyncMock()
    set_event_context(notification_service=notif)
    event = _make_event(
        EventType.HANDOFF_CREATED,
        task_id=str(uuid4()),
        handoff_id=str(uuid4()),
    )
    await handle_handoff_created(event)
    notif.send_handoff_notification.assert_not_called()


# ---------------------------------------------------------------------------
# QA result + blocker resolved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_qa_result_passed() -> None:
    """QA passed event triggers wait resolution if dev is waiting."""
    orch = MagicMock()
    orch.get_waiting_agents = MagicMock(return_value={})
    orch.resolve_wait = AsyncMock()
    set_event_context(orchestrator=orch)
    event = _make_event(
        EventType.TASK_QA_PASSED,
        task_id=str(uuid4()),
        assigned_to="be-dev-1",
    )
    await handle_qa_result(event)


@pytest.mark.asyncio
async def test_handle_blocker_resolved_logs() -> None:
    event = _make_event(
        EventType.TASK_UNBLOCKED,
        task_id=str(uuid4()),
        agent_id="be-dev-1",
        resolution="fixed",
    )
    await handle_blocker_resolved(event)


# ---------------------------------------------------------------------------
# Awaiting docs handler — no-team early return.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_awaiting_docs_skips_when_no_team() -> None:
    notif = MagicMock()
    notif.send_docs_ready_notification = AsyncMock()
    set_event_context(notification_service=notif)
    event = _make_event(EventType.TASK_AWAITING_DOCS, task_id=str(uuid4()))
    await handle_task_status_change(event)
    notif.send_docs_ready_notification.assert_not_called()


@pytest.mark.asyncio
async def test_handle_awaiting_qa_skips_when_no_team() -> None:
    notif = MagicMock()
    notif.send_qa_ready_notification = AsyncMock()
    set_event_context(notification_service=notif)
    event = _make_event(EventType.TASK_AWAITING_QA, task_id=str(uuid4()))
    await handle_task_status_change(event)
    notif.send_qa_ready_notification.assert_not_called()


@pytest.mark.asyncio
async def test_handle_task_qa_failed_without_context_is_noop() -> None:
    """No notification service set — TASK_QA_FAILED handler returns early."""
    event = _make_event(
        EventType.TASK_QA_FAILED, task_id=str(uuid4()), assigned_to="be-dev-1"
    )
    await handle_task_status_change(event)


@pytest.mark.asyncio
async def test_handle_awaiting_qa_without_context_is_noop() -> None:
    event = _make_event(
        EventType.TASK_AWAITING_QA, task_id=str(uuid4()), team="backend"
    )
    await handle_task_status_change(event)


@pytest.mark.asyncio
async def test_handle_awaiting_docs_without_context_is_noop() -> None:
    event = _make_event(
        EventType.TASK_AWAITING_DOCS, task_id=str(uuid4()), team="backend"
    )
    await handle_task_status_change(event)


# ---------------------------------------------------------------------------
# QA result + waiting agent resolution
# ---------------------------------------------------------------------------


@dataclass
class _FakeWaitRecord:
    waiting_for: str


@pytest.mark.asyncio
async def test_qa_result_resolves_waiting_developer() -> None:
    """When dev is waiting on `qa_result`, orchestrator.resolve_wait fires."""
    orch = MagicMock()
    orch.get_waiting_agents = MagicMock(
        return_value={"be-dev-1": _FakeWaitRecord(waiting_for="qa_result")}
    )
    orch.resolve_wait = AsyncMock()
    set_event_context(orchestrator=orch)

    event = _make_event(
        EventType.TASK_QA_PASSED,
        task_id=str(uuid4()),
        assigned_to="be-dev-1",
        qa_notes="lgtm",
    )
    await handle_qa_result(event)
    orch.resolve_wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_qa_result_does_not_resolve_when_not_waiting() -> None:
    orch = MagicMock()
    orch.get_waiting_agents = MagicMock(return_value={})
    orch.resolve_wait = AsyncMock()
    set_event_context(orchestrator=orch)

    event = _make_event(
        EventType.TASK_QA_PASSED,
        task_id=str(uuid4()),
        assigned_to="be-dev-1",
    )
    await handle_qa_result(event)
    orch.resolve_wait.assert_not_called()


@pytest.mark.asyncio
async def test_qa_result_skips_when_developer_id_missing() -> None:
    """Without `assigned_to`, _try_resolve_agent_wait short-circuits."""
    orch = MagicMock()
    orch.get_waiting_agents = MagicMock(return_value={})
    orch.resolve_wait = AsyncMock()
    set_event_context(orchestrator=orch)

    event = _make_event(EventType.TASK_QA_PASSED, task_id=str(uuid4()))
    await handle_qa_result(event)
    orch.resolve_wait.assert_not_called()


@pytest.mark.asyncio
async def test_qa_result_no_orchestrator_is_noop() -> None:
    event = _make_event(
        EventType.TASK_QA_PASSED,
        task_id=str(uuid4()),
        assigned_to="be-dev-1",
    )
    # No orchestrator wired in.
    await handle_qa_result(event)


@pytest.mark.asyncio
async def test_qa_result_waiting_for_other_thing_is_noop() -> None:
    """Dev is waiting, but for blocker_resolution — qa_result event ignores them."""
    orch = MagicMock()
    orch.get_waiting_agents = MagicMock(
        return_value={"be-dev-1": _FakeWaitRecord(waiting_for="blocker_resolution")}
    )
    orch.resolve_wait = AsyncMock()
    set_event_context(orchestrator=orch)

    event = _make_event(
        EventType.TASK_QA_PASSED,
        task_id=str(uuid4()),
        assigned_to="be-dev-1",
    )
    await handle_qa_result(event)
    orch.resolve_wait.assert_not_called()


# ---------------------------------------------------------------------------
# Question answered handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_question_answered_resolves_waiting_agent() -> None:
    orch = MagicMock()
    orch.get_waiting_agents = MagicMock(
        return_value={"be-dev-1": _FakeWaitRecord(waiting_for="answer")}
    )
    orch.resolve_wait = AsyncMock()
    set_event_context(orchestrator=orch)

    event = _make_event(
        EventType.QUESTION_ANSWERED,
        question_id=str(uuid4()),
        asking_agent="be-dev-1",
        answer="42",
    )
    await handle_question_answered(event)
    orch.resolve_wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_blocker_resolved_resolves_waiting_agent() -> None:
    orch = MagicMock()
    orch.get_waiting_agents = MagicMock(
        return_value={"be-dev-1": _FakeWaitRecord(waiting_for="blocker_resolution")}
    )
    orch.resolve_wait = AsyncMock()
    set_event_context(orchestrator=orch)

    event = _make_event(
        EventType.BLOCKER_RESOLVED,
        task_id=str(uuid4()),
        agent_id="be-dev-1",
        resolution="fixed",
    )
    await handle_blocker_resolved(event)
    orch.resolve_wait.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_event_context + register_default_handlers
# ---------------------------------------------------------------------------


def test_get_event_context_returns_singleton() -> None:
    a = get_event_context()
    b = get_event_context()
    assert a is b


def test_register_default_handlers_subscribes_each_event() -> None:
    """register_default_handlers wires every documented EventType."""
    bus = MagicMock()
    bus.subscribe = MagicMock()
    register_default_handlers(bus=bus)

    subscribed = [call.args[0] for call in bus.subscribe.call_args_list]
    assert EventType.TASK_BLOCKED in subscribed
    assert EventType.TASK_QA_PASSED in subscribed
    assert EventType.HANDOFF_CREATED in subscribed
    assert EventType.QUESTION_ANSWERED in subscribed


def test_register_default_handlers_uses_global_bus_when_none_passed() -> None:
    """When `bus=None`, it falls back to `get_event_bus()`."""
    fake_bus = MagicMock()
    fake_bus.subscribe = MagicMock()

    with patch("roboco.events.handlers.get_event_bus", return_value=fake_bus):
        register_default_handlers()
    fake_bus.subscribe.assert_called()

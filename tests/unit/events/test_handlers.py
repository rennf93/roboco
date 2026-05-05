"""Event handler coverage — fanout to notification service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.events.bus import Event, EventType
from roboco.events.handlers import (
    _get_doc_id,
    _get_pm_id,
    _get_qa_id,
    handle_blocker_resolved,
    handle_handoff_created,
    handle_qa_result,
    handle_session_boundary,
    handle_task_status_change,
    set_event_context,
)


def _make_event(event_type: EventType, **data) -> Event:
    return Event(
        type=event_type,
        data=data,
        source_agent="be-dev-1",
    )


@pytest.fixture(autouse=True)
def reset_context():
    """Reset event context after each test."""
    yield
    set_event_context(notification_service=None, orchestrator=None)


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
        EventType.TASK_BLOCKED, task_id=str(uuid4()), team="backend", reason="x"
    )
    await handle_task_status_change(event)
    notif.send_blocker_notification.assert_called_once()


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
        EventType.TASK_AWAITING_QA, task_id=str(uuid4()), team="backend"
    )
    await handle_task_status_change(event)
    notif.send_qa_ready_notification.assert_called_once()


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
    )
    await handle_task_status_change(event)
    notif.send_qa_failed_notification.assert_called_once()


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
        EventType.TASK_AWAITING_DOCS, task_id=str(uuid4()), team="backend"
    )
    await handle_task_status_change(event)
    notif.send_docs_ready_notification.assert_called_once()


@pytest.mark.asyncio
async def test_handle_task_status_change_unknown_type_noop() -> None:
    """No mapping for TASK_CREATED — does nothing."""
    event = _make_event(EventType.TASK_CREATED, task_id=str(uuid4()))
    await handle_task_status_change(event)  # No raise.


# ---------------------------------------------------------------------------
# Session and handoff handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_session_boundary_logs_and_returns() -> None:
    """Just exercises logging — no notification fanout in this handler."""
    event = _make_event(
        EventType.SESSION_CLOSED,
        session_id=str(uuid4()),
        group_id=str(uuid4()),
        reason="timeout",
    )
    await handle_session_boundary(event)


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
    )
    await handle_handoff_created(event)
    notif.send_handoff_notification.assert_called_once()


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

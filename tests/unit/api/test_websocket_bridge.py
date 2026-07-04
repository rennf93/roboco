"""websocket_bridge coverage — event handlers + bridge starter.

The handlers fan events from the Redis-stream bus to per-recipient WebSocket
connections. We don't need real Redis or sockets; we patch `manager` and the
`broadcast_*` helpers so each handler exercises its branches against
in-memory state.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.api.websocket_bridge import (
    _handle_a2a_message_event,
    _handle_agent_event,
    _handle_notification_sent,
    _handle_rate_limit_event,
    _handle_usage_event,
    register_websocket_bridge_handlers,
    start_websocket_bridge,
)
from roboco.models.events import Event, EventType


def _evt(event_type: EventType, data: dict, source_agent: str | None = None) -> Event:
    return Event(type=event_type, data=data, source_agent=source_agent)


# ---------------------------------------------------------------------------
# _handle_notification_sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_notification_sent_skips_when_missing_ids() -> None:
    """Incomplete event (missing recipient/notification IDs) → log + return."""
    event = _evt(EventType.NOTIFICATION_SENT, {})  # No notification_id/recipient_id
    with patch("roboco.api.websocket_bridge.broadcast_notification") as bcast:
        await _handle_notification_sent(event)
    bcast.assert_not_called()


@pytest.mark.asyncio
async def test_handle_notification_sent_skips_invalid_uuid() -> None:
    """Invalid UUID strings → log error + return without broadcasting."""
    event = _evt(
        EventType.NOTIFICATION_SENT,
        {"notification_id": "not-a-uuid", "recipient_id": str(uuid4())},
    )
    with patch("roboco.api.websocket_bridge.broadcast_notification") as bcast:
        await _handle_notification_sent(event)
    bcast.assert_not_called()


@pytest.mark.asyncio
async def test_handle_notification_sent_skips_when_no_connections() -> None:
    """Recipient has no WS connections → no broadcast."""
    nid = uuid4()
    rid = uuid4()
    event = _evt(
        EventType.NOTIFICATION_SENT,
        {
            "notification_id": str(nid),
            "recipient_id": str(rid),
            "type": "blocker",
            "subject": "x",
            "priority": "high",
        },
    )
    with (
        patch("roboco.api.websocket_bridge.broadcast_notification") as bcast,
        patch("roboco.api.websocket_bridge.manager") as mgr,
    ):
        mgr.notification_connections = {}  # No connections for any agent.
        await _handle_notification_sent(event)
    bcast.assert_not_called()


@pytest.mark.asyncio
async def test_handle_notification_sent_broadcasts_when_connected() -> None:
    """Recipient has WS connection → broadcast_notification called."""
    nid = uuid4()
    rid = uuid4()
    event = _evt(
        EventType.NOTIFICATION_SENT,
        {
            "notification_id": str(nid),
            "recipient_id": str(rid),
            "type": "qa_ready",
            "subject": "Task ready",
            "priority": "normal",
        },
    )
    bcast = AsyncMock()
    with (
        patch("roboco.api.websocket_bridge.broadcast_notification", bcast),
        patch("roboco.api.websocket_bridge.manager") as mgr,
    ):
        mgr.notification_connections = {rid: {"socket-1"}}  # Has a connection.
        await _handle_notification_sent(event)
    bcast.assert_awaited_once()
    assert bcast.await_args is not None
    call_kwargs = bcast.await_args.kwargs
    assert call_kwargs["notification_id"] == nid
    assert call_kwargs["agent_ids"] == [rid]


@pytest.mark.asyncio
async def test_handle_notification_acked_broadcasts_using_agent_id() -> None:
    """ACKED events carry `agent_id`, not `recipient_id`; the shared handler
    must still forward (to the acking agent) rather than log 'Incomplete
    notification event' on every acknowledgement."""
    nid = uuid4()
    aid = uuid4()
    event = _evt(
        EventType.NOTIFICATION_ACKED,
        {"notification_id": str(nid), "agent_id": str(aid), "ack_type": "read"},
    )
    bcast = AsyncMock()
    with (
        patch("roboco.api.websocket_bridge.broadcast_notification", bcast),
        patch("roboco.api.websocket_bridge.manager") as mgr,
    ):
        mgr.notification_connections = {aid: {"socket-1"}}
        await _handle_notification_sent(event)
    bcast.assert_awaited_once()
    assert bcast.await_args is not None
    call_kwargs = bcast.await_args.kwargs
    assert call_kwargs["notification_id"] == nid
    assert call_kwargs["agent_ids"] == [aid]


# ---------------------------------------------------------------------------
# _handle_agent_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_agent_event_skips_when_no_agent_id() -> None:
    event = _evt(EventType.AGENT_SPAWNED, {})
    with patch("roboco.api.websocket_bridge.manager") as mgr:
        mgr.broadcast_to_agent_watchers = AsyncMock()
        await _handle_agent_event(event)
        mgr.broadcast_to_agent_watchers.assert_not_called()


@pytest.mark.asyncio
async def test_handle_agent_event_skips_invalid_uuid() -> None:
    event = _evt(EventType.AGENT_SPAWNED, {"agent_id": "bad"})
    with patch("roboco.api.websocket_bridge.manager") as mgr:
        mgr.broadcast_to_agent_watchers = AsyncMock()
        await _handle_agent_event(event)
        mgr.broadcast_to_agent_watchers.assert_not_called()


@pytest.mark.asyncio
async def test_handle_agent_event_uses_source_agent_fallback() -> None:
    """When data has no agent_id, falls back to event.source_agent."""
    aid = uuid4()
    event = _evt(EventType.AGENT_STOPPED, {}, source_agent=str(aid))
    with patch("roboco.api.websocket_bridge.manager") as mgr:
        mgr.agent_connections = {aid: {"sock"}}
        mgr.broadcast_to_agent_watchers = AsyncMock()
        await _handle_agent_event(event)
        mgr.broadcast_to_agent_watchers.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_agent_event_skips_when_no_connections() -> None:
    aid = uuid4()
    event = _evt(EventType.AGENT_SPAWNED, {"agent_id": str(aid)})
    with patch("roboco.api.websocket_bridge.manager") as mgr:
        mgr.agent_connections = {}
        mgr.broadcast_to_agent_watchers = AsyncMock()
        await _handle_agent_event(event)
        mgr.broadcast_to_agent_watchers.assert_not_called()


@pytest.mark.asyncio
async def test_handle_agent_event_broadcasts() -> None:
    aid = uuid4()
    event = _evt(EventType.AGENT_RESUMED, {"agent_id": str(aid)})
    with patch("roboco.api.websocket_bridge.manager") as mgr:
        mgr.agent_connections = {aid: {"sock"}}
        mgr.broadcast_to_agent_watchers = AsyncMock()
        await _handle_agent_event(event)
        mgr.broadcast_to_agent_watchers.assert_awaited_once()
        call_args = mgr.broadcast_to_agent_watchers.await_args
        assert call_args.args[0] == aid
        assert call_args.args[1]["type"] == "agent.resumed"


# ---------------------------------------------------------------------------
# _handle_rate_limit_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_rate_limit_hit_broadcasts_to_system() -> None:
    """RATE_LIMIT_HIT → broadcast_system tagged with the type, payload intact."""
    retry_after = 60.0
    event = _evt(
        EventType.RATE_LIMIT_HIT,
        {
            "provider": "anthropic",
            "affectedAgents": ["be-dev-1"],
            "retryAfterSeconds": retry_after,
            "timestamp": "2026-06-11T00:00:00+00:00",
        },
    )
    with patch("roboco.api.websocket_bridge.manager") as mgr:
        mgr.broadcast_system = AsyncMock()
        await _handle_rate_limit_event(event)
    mgr.broadcast_system.assert_awaited_once()
    msg = mgr.broadcast_system.await_args.args[0]
    assert msg["type"] == "RATE_LIMIT_HIT"
    assert msg["provider"] == "anthropic"
    assert msg["affectedAgents"] == ["be-dev-1"]
    assert msg["retryAfterSeconds"] == retry_after


@pytest.mark.asyncio
async def test_handle_rate_limit_lifted_broadcasts_to_system() -> None:
    event = _evt(
        EventType.RATE_LIMIT_LIFTED,
        {"provider": "anthropic", "timestamp": "2026-06-11T00:01:00+00:00"},
    )
    with patch("roboco.api.websocket_bridge.manager") as mgr:
        mgr.broadcast_system = AsyncMock()
        await _handle_rate_limit_event(event)
    msg = mgr.broadcast_system.await_args.args[0]
    assert msg["type"] == "RATE_LIMIT_LIFTED"
    assert msg["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_handle_rate_limit_ignores_unrelated_event() -> None:
    """A non-rate-limit event type is a no-op (defensive guard)."""
    event = _evt(EventType.AGENT_SPAWNED, {"provider": "anthropic"})
    with patch("roboco.api.websocket_bridge.manager") as mgr:
        mgr.broadcast_system = AsyncMock()
        await _handle_rate_limit_event(event)
    mgr.broadcast_system.assert_not_called()


# ---------------------------------------------------------------------------
# _handle_usage_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_usage_snapshot_broadcasts_to_system() -> None:
    """USAGE_SNAPSHOT event → broadcast_system tagged USAGE_SNAPSHOT + aggregate."""
    expected_input = 500
    event = _evt(
        EventType.USAGE_SNAPSHOT,
        {
            "period": "live",
            "totals": {"input_tokens": expected_input, "output_tokens": 200},
            "cost_estimate": 0.0025,
            "by_agent": [
                {
                    "agent_id": "be-dev-1",
                    "input_tokens": 500,
                    "output_tokens": 200,
                    "model": "sonnet",
                    "cost_estimate": 0.0025,
                }
            ],
            "timestamp": "2026-06-11T00:01:00+00:00",
        },
    )
    with patch("roboco.api.websocket_bridge.manager") as mgr:
        mgr.broadcast_system = AsyncMock()
        await _handle_usage_event(event)
    mgr.broadcast_system.assert_awaited_once()
    msg = mgr.broadcast_system.await_args.args[0]
    assert msg["type"] == "USAGE_SNAPSHOT"
    assert msg["period"] == "live"
    assert msg["totals"]["input_tokens"] == expected_input
    assert len(msg["by_agent"]) == 1


# ---------------------------------------------------------------------------
# _handle_a2a_message_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_a2a_message_event_broadcasts_to_system() -> None:
    """An A2A_MESSAGE_SENT event is forwarded to /ws/system as an
    `a2a.message` frame — the CEO's live view of every agent-to-agent chat."""
    event = _evt(
        EventType.A2A_MESSAGE_SENT,
        {
            "conversation_id": "conv-1",
            "message_id": "msg-1",
            "task_id": "task-1",
            "from_agent": "be-dev-1",
            "to_agent": "be-qa",
            "skill": "code_review",
            "body_excerpt": "please review",
            "timestamp": "2026-07-02T00:00:00+00:00",
        },
    )
    with patch("roboco.api.websocket_bridge.manager") as mgr:
        mgr.broadcast_system = AsyncMock()
        await _handle_a2a_message_event(event)
    mgr.broadcast_system.assert_awaited_once()
    msg = mgr.broadcast_system.await_args.args[0]
    assert msg["type"] == "a2a.message"
    assert msg["conversation_id"] == "conv-1"
    assert msg["message_id"] == "msg-1"
    assert msg["task_id"] == "task-1"
    assert msg["from_agent"] == "be-dev-1"
    assert msg["to_agent"] == "be-qa"
    assert msg["skill"] == "code_review"
    assert msg["body_excerpt"] == "please review"
    assert msg["timestamp"] == "2026-07-02T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Registration + start
# ---------------------------------------------------------------------------


def test_register_websocket_bridge_handlers_subscribes_all_event_types() -> None:
    """Registration wires up all handler categories, including usage events."""

    class _FakeBus:
        def __init__(self) -> None:
            self.subscribed: list[tuple[EventType, object]] = []

        def subscribe(self, event_type: EventType, handler: object) -> None:
            self.subscribed.append((event_type, handler))

    fake = _FakeBus()
    with patch("roboco.api.websocket_bridge.get_event_bus", return_value=fake):
        register_websocket_bridge_handlers()
    types = [t for t, _ in fake.subscribed]
    # All expected event types appear at least once.
    assert EventType.NOTIFICATION_SENT in types
    assert EventType.NOTIFICATION_ACKED in types
    assert EventType.AGENT_SPAWNED in types
    assert EventType.AGENT_STOPPED in types
    assert EventType.AGENT_WAITING in types
    assert EventType.AGENT_RESUMED in types
    assert EventType.AGENT_ERROR in types
    assert EventType.RATE_LIMIT_HIT in types
    assert EventType.RATE_LIMIT_LIFTED in types
    # Usage events forwarded to /ws/system.
    assert EventType.USAGE_SNAPSHOT in types
    # A2A live chat forwarded to /ws/system (CEO live view).
    assert EventType.A2A_MESSAGE_SENT in types


@pytest.mark.asyncio
async def test_start_websocket_bridge_registers_handlers() -> None:
    """start_websocket_bridge() calls register_websocket_bridge_handlers."""
    with patch("roboco.api.websocket_bridge.register_websocket_bridge_handlers") as reg:
        await start_websocket_bridge()
    reg.assert_called_once()

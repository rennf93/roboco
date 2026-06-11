"""
WebSocket Event Bridge

Consumes events from Redis Streams and forwards them to WebSocket clients.
This enables real-time updates to connected clients while maintaining
durable message delivery through the stream.
"""

from uuid import UUID

import structlog

from roboco.api.websocket import broadcast_notification, manager
from roboco.events import Event, EventType, get_event_bus

logger = structlog.get_logger()

_RATE_LIMIT_WS_TYPES = {
    EventType.RATE_LIMIT_HIT: "RATE_LIMIT_HIT",
    EventType.RATE_LIMIT_LIFTED: "RATE_LIMIT_LIFTED",
}


# Handler for notification events
async def _handle_notification_sent(event: Event) -> None:
    """Handle NOTIFICATION_SENT events and forward to WebSocket."""
    data = event.data

    notification_id_str = data.get("notification_id")
    # SENT events carry `recipient_id`; ACKED events carry `agent_id` (the
    # agent who acknowledged). This handler serves both, so accept either —
    # otherwise every acknowledgement logged a spurious "Incomplete
    # notification event" and never reached the panel.
    recipient_id_str = data.get("recipient_id") or data.get("agent_id")
    notification_type = data.get("type", "unknown")
    subject = data.get("subject", "")
    priority = data.get("priority", "normal")

    if not notification_id_str or not recipient_id_str:
        logger.warning(
            "Incomplete notification event",
            event_id=str(event.id),
        )
        return

    try:
        notification_id = UUID(notification_id_str)
        recipient_id = UUID(recipient_id_str)
    except ValueError as e:
        logger.error("Invalid UUID in notification event", error=str(e))
        return

    # Check if recipient has WebSocket connections
    connections = manager.notification_connections.get(recipient_id, set())
    if connections:
        await broadcast_notification(
            agent_ids=[recipient_id],
            notification_id=notification_id,
            notification_type=notification_type,
            subject=subject,
            priority=priority,
        )
        logger.debug(
            "Notification forwarded to WebSocket",
            notification_id=notification_id_str,
            recipient=recipient_id_str,
            connection_count=len(connections),
        )


async def _handle_session_event(event: Event) -> None:
    """Handle session lifecycle events and forward to WebSocket."""
    data = event.data

    session_id_str = data.get("session_id")
    if not session_id_str:
        return

    try:
        session_id = UUID(session_id_str)
    except ValueError:
        return

    connections = manager.session_connections.get(session_id, set())
    if not connections:
        return

    # Forward event to session subscribers
    event_payload = {
        "type": f"session.{event.type.value.split('.')[-1]}",
        "session_id": session_id_str,
        "data": data,
    }

    await manager.broadcast_to_session(session_id, event_payload)
    logger.debug(
        "Session event forwarded to WebSocket",
        event_type=event.type.value,
        session_id=session_id_str,
    )


async def _handle_agent_event(event: Event) -> None:
    """Handle agent lifecycle events and forward to WebSocket."""
    data = event.data

    agent_id_str = data.get("agent_id") or event.source_agent
    if not agent_id_str:
        return

    try:
        agent_id = UUID(agent_id_str)
    except ValueError:
        return

    connections = manager.agent_connections.get(agent_id, set())
    if not connections:
        return

    event_payload = {
        "type": f"agent.{event.type.value.split('.')[-1]}",
        "agent_id": agent_id_str,
        "data": data,
    }

    await manager.broadcast_to_agent_watchers(agent_id, event_payload)
    logger.debug(
        "Agent event forwarded to WebSocket",
        event_type=event.type.value,
        agent_id=agent_id_str,
    )


async def _handle_rate_limit_event(event: Event) -> None:
    """Forward RATE_LIMIT_HIT/LIFTED events to operator system WS clients.

    The published payload already carries the panel's fields
    (``provider``, ``affectedAgents``, ``retryAfterSeconds``, ``timestamp``);
    we only tag it with the discriminating ``type`` the panel switches on.
    """
    ws_type = _RATE_LIMIT_WS_TYPES.get(event.type)
    if ws_type is None:
        return
    await manager.broadcast_system({"type": ws_type, **event.data})


async def _handle_usage_event(event: Event) -> None:
    """Forward USAGE_UPDATE/SNAPSHOT events to operator system WS clients.

    Both event types carry all the fields the panel needs directly in
    ``event.data``; we pass them through tagged with ``event.type.value``
    so the panel can distinguish per-agent updates from aggregate snapshots.
    """
    await manager.broadcast_system({"type": event.type.value, **event.data})
    logger.debug(
        "Usage event forwarded to system WebSocket",
        event_type=event.type.value,
    )


def register_websocket_bridge_handlers() -> None:
    """
    Register event handlers that forward events to WebSocket clients.

    Call this during application startup after the event bus is initialized.
    """
    bus = get_event_bus()

    # Notification events -> WebSocket
    bus.subscribe(EventType.NOTIFICATION_SENT, _handle_notification_sent)
    bus.subscribe(EventType.NOTIFICATION_ACKED, _handle_notification_sent)

    # Session events -> WebSocket
    bus.subscribe(EventType.SESSION_CREATED, _handle_session_event)
    bus.subscribe(EventType.SESSION_CLOSED, _handle_session_event)
    bus.subscribe(EventType.SESSION_TIMEOUT, _handle_session_event)

    # Agent events -> WebSocket
    bus.subscribe(EventType.AGENT_SPAWNED, _handle_agent_event)
    bus.subscribe(EventType.AGENT_STOPPED, _handle_agent_event)
    bus.subscribe(EventType.AGENT_WAITING, _handle_agent_event)
    bus.subscribe(EventType.AGENT_RESUMED, _handle_agent_event)
    bus.subscribe(EventType.AGENT_ERROR, _handle_agent_event)

    # Rate-limit lifecycle -> system WebSocket (panel banner)
    bus.subscribe(EventType.RATE_LIMIT_HIT, _handle_rate_limit_event)
    bus.subscribe(EventType.RATE_LIMIT_LIFTED, _handle_rate_limit_event)

    # Usage events -> system WebSocket (panel dashboard)
    bus.subscribe(EventType.USAGE_UPDATE, _handle_usage_event)
    bus.subscribe(EventType.USAGE_SNAPSHOT, _handle_usage_event)

    logger.info("WebSocket bridge handlers registered")


async def start_websocket_bridge() -> None:
    """
    Start the WebSocket bridge.

    This registers handlers and ensures they're connected to the event stream.
    Should be called during application startup.
    """
    register_websocket_bridge_handlers()
    logger.info("WebSocket bridge started")

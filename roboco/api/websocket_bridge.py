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

_USAGE_WS_TYPES = {
    EventType.USAGE_SNAPSHOT: "USAGE_SNAPSHOT",
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
    """Forward USAGE_SNAPSHOT events to operator system WS clients.

    The event carries all the fields the panel needs directly in
    ``event.data``; we tag it with the discriminating ``type`` string the
    panel switches on (the same UPPER_SNAKE mapping the rate-limit handler
    uses).
    """
    ws_type = _USAGE_WS_TYPES.get(event.type)
    if ws_type is None:
        return
    await manager.broadcast_system({"type": ws_type, **event.data})
    logger.debug(
        "Usage event forwarded to system WebSocket",
        event_type=ws_type,
    )


async def _handle_a2a_message_event(event: Event) -> None:
    """Forward an A2A_MESSAGE_SENT event to operator /ws/system clients as an
    `a2a.message` frame — the CEO's live view of every agent-to-agent chat.
    Carries only the excerpt the service already capped; the full body stays
    readable via the admin REST endpoints.
    """
    data = event.data
    await manager.broadcast_system(
        {
            "type": "a2a.message",
            "conversation_id": data.get("conversation_id"),
            "message_id": data.get("message_id"),
            "task_id": data.get("task_id"),
            "from_agent": data.get("from_agent"),
            "to_agent": data.get("to_agent"),
            "skill": data.get("skill"),
            "body_excerpt": data.get("body_excerpt", ""),
            "timestamp": data.get("timestamp"),
        }
    )
    logger.debug(
        "A2A message forwarded to system WebSocket",
        message_id=data.get("message_id"),
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
    bus.subscribe(EventType.USAGE_SNAPSHOT, _handle_usage_event)

    # A2A live chat -> operator system WebSocket (CEO live view)
    bus.subscribe(EventType.A2A_MESSAGE_SENT, _handle_a2a_message_event)

    logger.info("WebSocket bridge handlers registered")


async def start_websocket_bridge() -> None:
    """
    Start the WebSocket bridge.

    This registers handlers and ensures they're connected to the event stream.
    Should be called during application startup.
    """
    register_websocket_bridge_handlers()
    logger.info("WebSocket bridge started")

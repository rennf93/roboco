"""
WebSocket Handlers

Real-time communication via WebSocket connections for:
- Channel streams (all messages in a channel)
- Agent streams (individual agent output)
- Session streams (messages in a session)

Security Note:
    WebSocket connections validate agent_id via query params and verify
    the agent exists in the database. In production, this should be
    enhanced with proper token-based authentication (JWT, etc.).
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from roboco.api.schemas.websocket import (
    NewMessageBroadcast,
)
from roboco.config import settings
from roboco.db.base import get_db
from roboco.services.repositories import resolve_agent_uuid

router = APIRouter()


# =============================================================================
# Connection Manager
# =============================================================================


class ConnectionManager:
    """
    Manages WebSocket connections organized by type and ID.

    Supports:
    - Channel subscriptions
    - Agent output streams
    - Session streams
    """

    def __init__(self) -> None:
        # channel_id -> set of websockets
        self.channel_connections: dict[UUID, set[WebSocket]] = {}

        # agent_id -> set of websockets
        self.agent_connections: dict[UUID, set[WebSocket]] = {}

        # session_id -> set of websockets
        self.session_connections: dict[UUID, set[WebSocket]] = {}

        # agent_id -> set of websockets (for notifications)
        self.notification_connections: dict[UUID, set[WebSocket]] = {}

        # websocket -> agent_id (for tracking who is connected)
        self.connection_agents: dict[WebSocket, UUID] = {}

    async def connect_channel(
        self, websocket: WebSocket, channel_id: UUID, agent_id: UUID
    ) -> None:
        """Connect to a channel stream."""
        await websocket.accept()

        if channel_id not in self.channel_connections:
            self.channel_connections[channel_id] = set()

        self.channel_connections[channel_id].add(websocket)
        self.connection_agents[websocket] = agent_id

    async def connect_agent(
        self, websocket: WebSocket, target_agent_id: UUID, viewer_agent_id: UUID
    ) -> None:
        """Connect to an agent's output stream."""
        await websocket.accept()

        if target_agent_id not in self.agent_connections:
            self.agent_connections[target_agent_id] = set()

        self.agent_connections[target_agent_id].add(websocket)
        self.connection_agents[websocket] = viewer_agent_id

    async def connect_session(
        self, websocket: WebSocket, session_id: UUID, agent_id: UUID
    ) -> None:
        """Connect to a session stream."""
        await websocket.accept()

        if session_id not in self.session_connections:
            self.session_connections[session_id] = set()

        self.session_connections[session_id].add(websocket)
        self.connection_agents[websocket] = agent_id

    async def connect_notifications(self, websocket: WebSocket, agent_id: UUID) -> None:
        """Connect to an agent's notification stream."""
        await websocket.accept()

        if agent_id not in self.notification_connections:
            self.notification_connections[agent_id] = set()

        self.notification_connections[agent_id].add(websocket)
        self.connection_agents[websocket] = agent_id

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a websocket from all subscriptions."""
        # Remove from channel connections
        for connections in self.channel_connections.values():
            connections.discard(websocket)

        # Remove from agent connections
        for connections in self.agent_connections.values():
            connections.discard(websocket)

        # Remove from session connections
        for connections in self.session_connections.values():
            connections.discard(websocket)

        # Remove from notification connections
        for connections in self.notification_connections.values():
            connections.discard(websocket)

        # Remove from tracking
        self.connection_agents.pop(websocket, None)

    async def broadcast_to_channel(
        self, channel_id: UUID, message: dict[str, Any]
    ) -> None:
        """Broadcast a message to all channel subscribers."""
        connections = self.channel_connections.get(channel_id, set())
        if not connections:
            return

        data = json.dumps(message, default=str)
        await asyncio.gather(
            *[conn.send_text(data) for conn in connections],
            return_exceptions=True,
        )

    async def broadcast_to_agent_watchers(
        self, agent_id: UUID, message: dict[str, Any]
    ) -> None:
        """Broadcast a message to all watching an agent's stream."""
        connections = self.agent_connections.get(agent_id, set())
        if not connections:
            return

        data = json.dumps(message, default=str)
        await asyncio.gather(
            *[conn.send_text(data) for conn in connections],
            return_exceptions=True,
        )

    async def broadcast_to_session(
        self, session_id: UUID, message: dict[str, Any]
    ) -> None:
        """Broadcast a message to all session subscribers."""
        connections = self.session_connections.get(session_id, set())
        if not connections:
            return

        data = json.dumps(message, default=str)
        await asyncio.gather(
            *[conn.send_text(data) for conn in connections],
            return_exceptions=True,
        )

    def get_channel_subscriber_count(self, channel_id: UUID) -> int:
        """Get number of subscribers to a channel."""
        return len(self.channel_connections.get(channel_id, set()))

    def get_agent_watcher_count(self, agent_id: UUID) -> int:
        """Get number of watchers of an agent's stream."""
        return len(self.agent_connections.get(agent_id, set()))


# Global connection manager
manager = ConnectionManager()


async def validate_agent_exists(agent_id: UUID | str) -> bool:
    """
    Validate that an agent exists in the database.

    This provides basic security by ensuring the claimed agent_id
    is a valid agent, not just a valid UUID format.

    TODO: Enhance with token-based authentication (JWT) for production.
    """
    try:
        async for db in get_db():
            result = await resolve_agent_uuid(db, str(agent_id))
            return result is not None
    except Exception:
        return False
    return False


async def validate_channel_access(channel_id: UUID, agent_id: UUID) -> bool:
    """
    Validate that an agent has access to a channel.

    Calls the permissions API to check read access.
    """
    try:
        url = f"http://{settings.host}:{settings.port}/api/permissions/check"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url,
                params={
                    "agent_id": str(agent_id),
                    "channel_id": str(channel_id),
                    "action": "read",
                },
            )
            if response.status_code == status.HTTP_200_OK:
                data = response.json()
                return bool(data.get("allowed", False))
            return False
    except Exception:
        # On error, deny access (fail closed)
        return False


# =============================================================================
# WebSocket Routes
# =============================================================================


@router.websocket("/channels/{channel_id}")
async def channel_stream(
    websocket: WebSocket,
    channel_id: UUID,
) -> None:
    """
    WebSocket endpoint for channel message streams.

    Clients receive real-time messages for the channel.
    """
    # Get agent ID from query params (or auth in production)
    agent_id_str = websocket.query_params.get("agent_id")
    if not agent_id_str:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        agent_id = UUID(agent_id_str)
    except ValueError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Validate agent access to channel
    has_access = await validate_channel_access(channel_id, agent_id)
    if not has_access:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect_channel(websocket, channel_id, agent_id)

    try:
        # Send connection confirmation
        await websocket.send_json(
            {
                "type": "connected",
                "channel_id": str(channel_id),
                "subscriber_count": manager.get_channel_subscriber_count(channel_id),
            }
        )

        # Keep connection alive and handle incoming messages
        while True:
            data = await websocket.receive_text()

            # Handle ping/pong for keepalive
            if data == "ping":
                await websocket.send_text("pong")
                continue

            # Handle other client messages if needed
            # For now, channels are primarily for receiving

    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.websocket("/agents/{agent_id}")
async def agent_stream(
    websocket: WebSocket,
    agent_id: UUID,
) -> None:
    """
    WebSocket endpoint for an agent's output stream.

    Clients receive real-time LLM output from the agent.
    """
    # Get viewer agent ID
    viewer_id_str = websocket.query_params.get("viewer_id")
    if not viewer_id_str:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        viewer_id = UUID(viewer_id_str)
    except ValueError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Validate viewer agent exists in database
    if not await validate_agent_exists(viewer_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect_agent(websocket, agent_id, viewer_id)

    try:
        await websocket.send_json(
            {
                "type": "connected",
                "agent_id": str(agent_id),
                "watcher_count": manager.get_agent_watcher_count(agent_id),
            }
        )

        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.websocket("/sessions/{session_id}")
async def session_stream(
    websocket: WebSocket,
    session_id: UUID,
) -> None:
    """
    WebSocket endpoint for session message streams.

    Clients receive real-time messages for a specific session.
    """
    agent_id_str = websocket.query_params.get("agent_id")
    if not agent_id_str:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        agent_id = UUID(agent_id_str)
    except ValueError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Validate agent exists in database
    if not await validate_agent_exists(agent_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect_session(websocket, session_id, agent_id)

    try:
        await websocket.send_json(
            {
                "type": "connected",
                "session_id": str(session_id),
            }
        )

        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.websocket("/notifications/{agent_id}")
async def notification_stream(
    websocket: WebSocket,
    agent_id: UUID,
) -> None:
    """
    WebSocket endpoint for agent notifications.

    Agents receive real-time notifications via this stream.
    """
    # Validate agent exists in database
    if not await validate_agent_exists(agent_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect_notifications(websocket, agent_id)

    try:
        await websocket.send_json(
            {
                "type": "connected",
                "agent_id": str(agent_id),
            }
        )

        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        manager.disconnect(websocket)


# =============================================================================
# Helper Functions for Broadcasting
# =============================================================================


async def broadcast_new_message(msg: NewMessageBroadcast) -> None:
    """Broadcast a new message to channel and session subscribers."""
    event = {
        "type": "message.new",
        "message_id": str(msg.message_id),
        "agent_id": str(msg.agent_id),
        "content": msg.content,
        "message_type": msg.message_type,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    await asyncio.gather(
        manager.broadcast_to_channel(msg.channel_id, event),
        manager.broadcast_to_session(msg.session_id, event),
    )


async def broadcast_agent_chunk(
    agent_id: str, chunk: str, metadata: dict[str, Any]
) -> None:
    """Broadcast an agent stream chunk to watchers."""
    event = {
        "type": "agent.stream",
        "agent_id": agent_id,
        "chunk": chunk,
        "timestamp": datetime.now(UTC).isoformat(),
        **metadata,
    }

    await manager.broadcast_to_agent_watchers(UUID(agent_id), event)


async def broadcast_session_closed(
    session_id: UUID, channel_id: UUID, reason: str
) -> None:
    """Broadcast session closed event."""
    event = {
        "type": "session.closed",
        "session_id": str(session_id),
        "reason": reason,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    await asyncio.gather(
        manager.broadcast_to_session(session_id, event),
        manager.broadcast_to_channel(channel_id, event),
    )


async def broadcast_notification(
    agent_ids: list[UUID],
    notification_id: UUID,
    notification_type: str,
    subject: str,
    priority: str,
) -> None:
    """
    Broadcast notification to specific agents.

    Sends to all agents that have notification websocket connections.
    """
    event = {
        "type": "notification",
        "notification_id": str(notification_id),
        "notification_type": notification_type,
        "subject": subject,
        "priority": priority,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    data = json.dumps(event)

    for agent_id in agent_ids:
        connections = manager.notification_connections.get(agent_id, set())
        if connections:
            await asyncio.gather(
                *[conn.send_text(data) for conn in connections],
                return_exceptions=True,
            )

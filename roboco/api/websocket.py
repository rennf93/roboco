"""
WebSocket Handlers

Real-time communication via WebSocket connections for:
- Channel streams (all messages in a channel)
- Agent streams (individual agent output)
- Session streams (messages in a session)
"""

import asyncio
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

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


# =============================================================================
# WebSocket Message Types
# =============================================================================


class WSMessage(BaseModel):
    """Base WebSocket message."""

    type: str
    timestamp: datetime = datetime.utcnow()


class WSMessageNew(WSMessage):
    """New message event."""

    type: str = "message.new"
    message_id: UUID
    agent_id: UUID
    content: str
    message_type: str


class WSMessageEdit(WSMessage):
    """Message edited event."""

    type: str = "message.edit"
    message_id: UUID
    content: str


class WSMessageDelete(WSMessage):
    """Message deleted event."""

    type: str = "message.delete"
    message_id: UUID


class WSAgentStream(WSMessage):
    """Agent stream chunk."""

    type: str = "agent.stream"
    agent_id: UUID
    chunk: str


class WSSessionClosed(WSMessage):
    """Session closed event."""

    type: str = "session.closed"
    session_id: UUID
    reason: str


class WSNotification(WSMessage):
    """Notification event."""

    type: str = "notification"
    notification_id: UUID
    notification_type: str
    subject: str
    priority: str


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
    # TODO: Validate agent access to channel
    # For now, accept all connections

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


# =============================================================================
# Helper Functions for Broadcasting
# =============================================================================


async def broadcast_new_message(
    channel_id: UUID,
    session_id: UUID,
    message_id: UUID,
    agent_id: UUID,
    content: str,
    message_type: str,
) -> None:
    """Broadcast a new message to channel and session subscribers."""
    event = {
        "type": "message.new",
        "message_id": str(message_id),
        "agent_id": str(agent_id),
        "content": content,
        "message_type": message_type,
        "timestamp": datetime.utcnow().isoformat(),
    }

    await asyncio.gather(
        manager.broadcast_to_channel(channel_id, event),
        manager.broadcast_to_session(session_id, event),
    )


async def broadcast_agent_chunk(agent_id: UUID, chunk: str) -> None:
    """Broadcast an agent stream chunk to watchers."""
    event = {
        "type": "agent.stream",
        "agent_id": str(agent_id),
        "chunk": chunk,
        "timestamp": datetime.utcnow().isoformat(),
    }

    await manager.broadcast_to_agent_watchers(agent_id, event)


async def broadcast_session_closed(
    session_id: UUID, channel_id: UUID, reason: str
) -> None:
    """Broadcast session closed event."""
    event = {
        "type": "session.closed",
        "session_id": str(session_id),
        "reason": reason,
        "timestamp": datetime.utcnow().isoformat(),
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

    Note: This requires per-agent connections, which we could add
    as a separate subscription type.
    """
    # TODO: Implement per-agent notification delivery
    # For now, agents must poll the notifications endpoint
    pass

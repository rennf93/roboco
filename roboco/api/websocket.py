"""
WebSocket Handlers

Real-time communication via WebSocket connections for:
- Agent streams (individual agent output)
- Notification streams (per-agent formal notifications)
- System stream (operator-wide: rate limits, usage, A2A live view)

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

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from roboco.agents_config import CEO_AGENT_ID, verify_agent_token
from roboco.api.auth.backend import SESSION_COOKIE_NAME
from roboco.api.auth.session import resolve_session_user
from roboco.api.deps import _auth_required
from roboco.config import settings
from roboco.db.base import get_db
from roboco.services.repositories import resolve_agent_uuid

router = APIRouter()
log = structlog.get_logger()

# Server-side idle timeout for WS receive loops. A half-open socket (dead
# agent container, silent client) blocks ``receive_text()`` forever;
# ``asyncio.wait_for`` reaps the socket after this many seconds of silence.
IDLE_TIMEOUT_SECONDS: float = 90.0

# Per-connection send queue + send timeout. Each registered connection owns a
# bounded ``asyncio.Queue`` drained by a sender task, so a slow client can't
# back-pressure the fan-out: broadcast enqueues (non-blocking) and returns
# immediately; a full queue drops + logs (client lagging, not the fan-out).
MAX_SEND_QUEUE: int = 256
SEND_TIMEOUT_SECONDS: float = 10.0


class _ClientConnection:
    """Per-connection send queue + sender task.

    Holds the bounded outbound queue drained by ``sender``; broadcast enqueues
    here instead of awaiting ``send_text`` directly, so one slow client cannot
    block the fan-out to every other client.
    """

    __slots__ = ("queue", "sender", "websocket")

    def __init__(self, websocket: WebSocket, maxsize: int) -> None:
        self.websocket = websocket
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self.sender: asyncio.Task[None] | None = None


async def _require_panel_token(websocket: WebSocket) -> bool:
    """Bind a per-agent WS upgrade to the panel/CEO HMAC token (dual-path).

    /ws/* streams are operator-only (the panel is the sole WS client; agents
    use MCP verbs). nginx injects the CEO panel token as ``X-Agent-Token``.
    In strict mode (``ROBOCO_AGENT_AUTH_REQUIRED=true``) the token is required
    + verified against the CEO identity; a presented-but-forged token is
    rejected even in dev mode. Returns True to proceed, False to close.

    When ``settings.cloud_auth_enabled``, the HMAC token still works exactly
    as above; absent one, a valid cloud-auth session cookie (Starlette's
    WebSocket exposes cookies parsed from the upgrade request's Cookie
    header) is accepted instead. Off => unchanged.
    """
    token = websocket.headers.get("x-agent-token")
    if not settings.cloud_auth_enabled:
        if _auth_required() and not token:
            return False
        # A missing token in dev mode proceeds; a presented token must verify.
        return not (token and not verify_agent_token(token, CEO_AGENT_ID, "ceo", ""))

    if token:
        return verify_agent_token(token, CEO_AGENT_ID, "ceo", "")
    cookie_value = websocket.cookies.get(SESSION_COOKIE_NAME)
    async for db in get_db():
        return await resolve_session_user(cookie_value, db) is not None
    return False


# =============================================================================
# Connection Manager
# =============================================================================


class ConnectionManager:
    """
    Manages WebSocket connections organized by type and ID.

    Supports:
    - Agent output streams
    - Notification streams
    - The operator-wide system stream
    """

    def __init__(self) -> None:
        # agent_id -> set of websockets
        self.agent_connections: dict[UUID, set[WebSocket]] = {}

        # agent_id -> set of websockets (for notifications)
        self.notification_connections: dict[UUID, set[WebSocket]] = {}

        # Operator/system-wide stream (rate limits, etc.) — no per-agent keying.
        self.system_connections: set[WebSocket] = set()

        # websocket -> agent_id (for tracking who is connected)
        self.connection_agents: dict[WebSocket, UUID] = {}

        # websocket -> per-connection send queue + sender task. Every connect_*
        # registers here; disconnect cancels + removes. Broadcast enqueues into
        # these queues instead of awaiting send_text directly so one slow client
        # can't block the fan-out.
        self.connection_senders: dict[WebSocket, _ClientConnection] = {}

        # Fire-and-forget fallback send tasks for unregistered sockets (legacy
        # path). Held to satisfy ruff RUF006 + allow clean shutdown; each task
        # removes itself on completion.
        self._pending_sends: set[asyncio.Task[None]] = set()

    def _register_sender(self, websocket: WebSocket) -> _ClientConnection:
        """Create the per-connection send queue + start its sender task."""
        conn = _ClientConnection(websocket, maxsize=MAX_SEND_QUEUE)
        conn.sender = asyncio.create_task(self._run_sender(conn))
        self.connection_senders[websocket] = conn
        return conn

    async def _run_sender(self, conn: _ClientConnection) -> None:
        """Drain the per-connection send queue; each send is timeout-bounded."""
        ws = conn.websocket
        while True:
            data = await conn.queue.get()
            try:
                await asyncio.wait_for(ws.send_text(data), timeout=SEND_TIMEOUT_SECONDS)
            except TimeoutError:
                log.warning(
                    "WebSocket send timeout — dropping message to slow client",
                    timeout=SEND_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                # Transport closed / hard send error — the socket is provably
                # dead on the send side. Reap it from every subscription set
                # now instead of waiting for the receive loop's idle timeout to
                # notice: otherwise the dead socket lingers in the sets and
                # every subsequent broadcast enqueues into this connection's
                # queue whose consumer has just exited (queue-overflow log spam
                # then silent drops) for up to IDLE_TIMEOUT_SECONDS. A send
                # TIMEOUT alone (slow client) does NOT reach here — it is
                # caught above and the live socket is kept.
                log.debug(
                    "WebSocket sender disconnecting on send error",
                    error=str(exc),
                )
                self.disconnect(ws)
                return

    async def connect_agent(
        self, websocket: WebSocket, target_agent_id: UUID, viewer_agent_id: UUID
    ) -> None:
        """Connect to an agent's output stream."""
        await websocket.accept()

        if target_agent_id not in self.agent_connections:
            self.agent_connections[target_agent_id] = set()

        self.agent_connections[target_agent_id].add(websocket)
        self.connection_agents[websocket] = viewer_agent_id
        self._register_sender(websocket)

    async def connect_notifications(self, websocket: WebSocket, agent_id: UUID) -> None:
        """Connect to an agent's notification stream."""
        await websocket.accept()

        if agent_id not in self.notification_connections:
            self.notification_connections[agent_id] = set()

        self.notification_connections[agent_id].add(websocket)
        self.connection_agents[websocket] = agent_id
        self._register_sender(websocket)

    async def connect_system(self, websocket: WebSocket) -> None:
        """Connect to the operator/system-wide stream (rate limits, etc.)."""
        await websocket.accept()
        self.system_connections.add(websocket)
        self._register_sender(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a websocket from all subscriptions."""
        # Remove from agent connections
        for connections in self.agent_connections.values():
            connections.discard(websocket)

        # Remove from notification connections
        for connections in self.notification_connections.values():
            connections.discard(websocket)

        # Remove from the system-wide stream
        self.system_connections.discard(websocket)

        # Remove from tracking
        self.connection_agents.pop(websocket, None)

        # Cancel + drop the per-connection sender task so a slow/stale client's
        # queue doesn't leak after the socket is removed.
        conn = self.connection_senders.pop(websocket, None)
        if conn is not None and conn.sender is not None:
            conn.sender.cancel()

    def _enqueue_or_send(self, websocket: WebSocket, data: str) -> None:
        """Fan out one message to one connection without blocking.

        Registered connections get the message enqueued into their bounded send
        queue (non-blocking, drop + warn on overflow). An unregistered socket
        falls back to a timeout-bounded ``send_text`` scheduled on the loop, so
        the broadcast never blocks on a single slow client.
        """
        conn = self.connection_senders.get(websocket)
        if conn is not None:
            try:
                conn.queue.put_nowait(data)
            except asyncio.QueueFull:
                log.warning(
                    "WebSocket send queue overflow — dropping message",
                    queue_size=conn.queue.maxsize,
                )
            return
        # Legacy fallback: schedule a timeout-bounded send so a slow
        # unregistered client can't wedge the fan-out either. Keep a strong
        # reference so the task isn't GC'd mid-flight (ruff RUF006); it
        # discards itself on completion.
        task = asyncio.create_task(self._send_with_timeout(websocket, data))
        self._pending_sends.add(task)
        task.add_done_callback(self._pending_sends.discard)

    async def _send_with_timeout(self, websocket: WebSocket, data: str) -> None:
        try:
            await asyncio.wait_for(
                websocket.send_text(data), timeout=SEND_TIMEOUT_SECONDS
            )
        except TimeoutError:
            log.warning(
                "WebSocket send timeout — dropping message to slow client",
                timeout=SEND_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # transport closed / cancelled
            log.debug("WebSocket send failed", error=str(exc))

    async def broadcast_to_agent_watchers(
        self, agent_id: UUID, message: dict[str, Any]
    ) -> None:
        """Broadcast a message to all watching an agent's stream."""
        connections = self.agent_connections.get(agent_id, set())
        if not connections:
            return
        data = json.dumps(message, default=str)
        for conn in connections:
            self._enqueue_or_send(conn, data)

    async def broadcast_system(self, message: dict[str, Any]) -> None:
        """Broadcast a message to all operator/system-wide subscribers."""
        if not self.system_connections:
            return
        data = json.dumps(message, default=str)
        for conn in self.system_connections:
            self._enqueue_or_send(conn, data)

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


# =============================================================================
# WebSocket Routes
# =============================================================================


@router.websocket("/agents/{agent_id}")
async def agent_stream(
    websocket: WebSocket,
    agent_id: UUID,
) -> None:
    """
    WebSocket endpoint for an agent's output stream.

    Clients receive real-time LLM output from the agent.
    """
    # Verify the panel/CEO token before any subject lookup.
    if not await _require_panel_token(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
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
            data = await asyncio.wait_for(
                websocket.receive_text(), timeout=IDLE_TIMEOUT_SECONDS
            )
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        # Clean client-initiated disconnect — handled here for clarity; the
        # finally below also disconnects (idempotent) to cover every other
        # exit path (anyio closed-resource, CancelledError, transport errors).
        pass
    except TimeoutError:
        # Idle timeout — the client has been silent for IDLE_TIMEOUT_SECONDS
        # (likely a half-open socket from a dead container). Fall through to
        # the finally so the socket is removed from every subscription set.
        log.warning(
            "WebSocket idle timeout — disconnecting", timeout=IDLE_TIMEOUT_SECONDS
        )
    finally:
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
    # Verify the panel/CEO token before any subject lookup.
    if not await _require_panel_token(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
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
            data = await asyncio.wait_for(
                websocket.receive_text(), timeout=IDLE_TIMEOUT_SECONDS
            )
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        # Clean client-initiated disconnect — handled here for clarity; the
        # finally below also disconnects (idempotent) to cover every other
        # exit path (anyio closed-resource, CancelledError, transport errors).
        pass
    except TimeoutError:
        # Idle timeout — the client has been silent for IDLE_TIMEOUT_SECONDS
        # (likely a half-open socket from a dead container). Fall through to
        # the finally so the socket is removed from every subscription set.
        log.warning(
            "WebSocket idle timeout — disconnecting", timeout=IDLE_TIMEOUT_SECONDS
        )
    finally:
        manager.disconnect(websocket)


@router.websocket("/system")
async def system_stream(websocket: WebSocket) -> None:
    """Operator/system-wide WebSocket stream.

    Carries system-level events for the control panel — currently the
    rate-limit lifecycle (``RATE_LIMIT_HIT`` / ``RATE_LIMIT_LIFTED``), bridged
    from the event bus by ``websocket_bridge``. No per-agent keying; the
    panel/CEO token gate matches every sibling /ws/* stream (#24 — this was the
    only ungated /ws endpoint): in strict mode a missing CEO token closes with
    policy-violation, a presented-but-forged token is rejected even in dev.
    """
    # Verify the panel/CEO token before subscribing — same gate as every other
    # /ws/* handler (#24).
    if not await _require_panel_token(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await manager.connect_system(websocket)

    try:
        await websocket.send_json({"type": "connected"})

        while True:
            data = await asyncio.wait_for(
                websocket.receive_text(), timeout=IDLE_TIMEOUT_SECONDS
            )
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        # Clean client-initiated disconnect — handled here for clarity; the
        # finally below also disconnects (idempotent) to cover every other
        # exit path (anyio closed-resource, CancelledError, transport errors).
        pass
    except TimeoutError:
        # Idle timeout — the client has been silent for IDLE_TIMEOUT_SECONDS
        # (likely a half-open socket from a dead container). Fall through to
        # the finally so the socket is removed from every subscription set.
        log.warning(
            "WebSocket idle timeout — disconnecting", timeout=IDLE_TIMEOUT_SECONDS
        )
    finally:
        manager.disconnect(websocket)


# =============================================================================
# Helper Functions for Broadcasting
# =============================================================================


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
            for conn in connections:
                manager._enqueue_or_send(conn, data)

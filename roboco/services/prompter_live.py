"""Live intake-session relay — the orchestrator side of the chat bridge.

A live intake session is one spawned ``prompter`` container the CEO is chatting
with. This registry connects three flows for that session, all in-process (the
orchestrator is single-process and already holds container state in memory):

- **agent -> panel:** the container's driver POSTs each normalized
  ``StreamChunk`` to the relay endpoint, which ``push()``es it onto the
  session's queue; the SSE endpoint ``stream()``s the queue to the browser.
- **panel -> agent:** the message endpoint ``deliver()``s the human's text to
  the container's in-process receiver over HTTP.
- **lifecycle:** ``open`` on spawn, ``close`` on reap (draft confirmed / idle);
  ``close`` unblocks any open stream with a sentinel.

This module owns no SDK or Claude code — it's pure plumbing, fully unit-tested.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()

# The in-container receiver port (same ROBOCO_SDK_PORT every agent sidecar uses).
SDK_PORT = 9000
# Sentinel pushed onto a session's queue to end its SSE stream.
_CLOSE = object()


@dataclass
class LiveIntakeSession:
    """One live chat: a queue of outbound events + the container to deliver to."""

    session_id: str
    agent_id: str  # container agent id, e.g. "intake-3f9c1a2b"
    queue: asyncio.Queue[Any] = field(default_factory=asyncio.Queue)
    closed: bool = False


class PrompterLiveRegistry:
    """Tracks live intake sessions and bridges panel <-> container."""

    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._sessions: dict[str, LiveIntakeSession] = {}
        self._client = http_client
        self.log = logger.bind(component="prompter_live")

    # -- lifecycle ---------------------------------------------------------

    def open(self, session_id: str, agent_id: str) -> LiveIntakeSession:
        """Register a live session (called when its container is spawned).

        Idempotent: if a live (un-closed) session already exists for this id,
        return it unchanged instead of replacing it with a fresh queue. A second
        ``open`` would otherwise orphan the SSE stream — ``stream()`` captures the
        session's queue once when the browser connects, so swapping in a new queue
        strands the browser on the old one while the agent's events push to the
        new one (the panel then shows nothing despite the agent replying).
        """
        existing = self._sessions.get(session_id)
        if existing is not None and not existing.closed:
            return existing
        session = LiveIntakeSession(session_id=session_id, agent_id=agent_id)
        self._sessions[session_id] = session
        self.log.info(
            "Live intake session opened", session_id=session_id, agent_id=agent_id
        )
        return session

    def get(self, session_id: str) -> LiveIntakeSession | None:
        return self._sessions.get(session_id)

    def is_alive(self, session_id: str) -> bool:
        """True when a live, un-closed session exists for this id.

        The panel calls this after a page reload to decide whether it can
        reconnect to a still-running intake agent rather than dropping the
        chat back to the scope form.
        """
        session = self._sessions.get(session_id)
        return session is not None and not session.closed

    def close(self, session_id: str) -> None:
        """End a live session and unblock its stream (called on reap)."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        session.closed = True
        session.queue.put_nowait(_CLOSE)
        self.log.info("Live intake session closed", session_id=session_id)

    # -- agent -> panel ----------------------------------------------------

    def push(self, session_id: str, event: dict[str, Any]) -> bool:
        """Queue one agent event for the SSE stream. False if no such session."""
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return False
        session.queue.put_nowait(event)
        return True

    async def stream(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield queued agent events until the session is closed."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        while True:
            item = await session.queue.get()
            if item is _CLOSE:
                return
            yield item

    # -- panel -> agent ----------------------------------------------------

    async def deliver(self, session_id: str, text: str) -> bool:
        """Deliver the human's message to the container's receiver. False if gone."""
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return False
        url = f"http://roboco-agent-{session.agent_id}:{SDK_PORT}/turn"
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.post(url, json={"text": text})
            resp.raise_for_status()
            return True
        except Exception as exc:
            # Debug, not error: the opening-message delivery retries until the
            # container's receiver is up, so transient failures here are expected
            # and were spamming ERROR. Callers surface a real failure (the
            # /messages route 404s; _deliver_when_ready warns once after N tries).
            self.log.debug(
                "Message delivery attempt failed", session_id=session_id, error=str(exc)
            )
            return False
        finally:
            if self._client is None:
                await client.aclose()


# Process-wide singleton — the orchestrator owns one registry. Held on a class
# (mirrors events/stream_bus._StreamEventBusHolder) to avoid a `global`.
class _RegistryHolder:
    instance: PrompterLiveRegistry | None = None


def get_live_registry() -> PrompterLiveRegistry:
    """Return the process-wide live-session registry."""
    if _RegistryHolder.instance is None:
        _RegistryHolder.instance = PrompterLiveRegistry()
    return _RegistryHolder.instance

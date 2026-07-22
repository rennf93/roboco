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
import time
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
# While the panel holds the SSE stream open, refresh ``last_activity`` on this
# cadence even with no events — an open stream means the human is watching (e.g.
# reading a proposed draft / MegaTask without typing), so the idle reaper must
# not retire the chat out from under them. Well under
# ``interactive_idle_reap_seconds`` (default 1800). When the tab closes the
# generator is cancelled, the refresh stops, and the chat reaps normally.
_STREAM_KEEPALIVE_SECONDS = 60.0


@dataclass
class LiveIntakeSession:
    """One live chat: a queue of outbound events + the container to deliver to."""

    session_id: str
    agent_id: str  # container agent id, e.g. "intake-3f9c1a2b"
    queue: asyncio.Queue[Any] = field(default_factory=asyncio.Queue)
    closed: bool = False
    # Set when the chat is *parked* awaiting board review of this task: the
    # session stays alive (not reaped) so the board's feedback can be injected
    # in-context for an in-place re-draft. ``None`` for a normal live chat.
    task_id: str | None = None
    # Monotonic timestamp of the last turn (agent->panel push or panel->agent
    # deliver). The idle-reap sweep uses this to retire an ABANDONED chat without
    # touching an active or page-reloaded one (reload reconnects + keeps
    # exchanging turns, so activity stays fresh). Seeded at open.
    last_activity: float = field(default_factory=time.monotonic)


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

    def has_live_agent(self, agent_id: str) -> bool:
        """True if any un-closed session is bound to ``agent_id``.

        Distinct from ``is_alive`` (which checks a *specific* session id the
        caller already holds): this answers "is the singleton agent behind
        ``agent_id`` live right now, under ANY session id" — e.g. so a device
        with no session of its own can tell "a Secretary chat is live
        elsewhere" apart from "nothing is running" before deciding whether to
        spawn a competing session against the same one-container singleton.
        """
        return any(
            not s.closed for s in self._sessions.values() if s.agent_id == agent_id
        )

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

    def close_by_agent(self, agent_id: str, *, error: str | None = None) -> list[str]:
        """Close every live session bound to ``agent_id`` (e.g. on a forced kill).

        When the orchestrator kills an interactive container out-of-band (the
        Grok cost-cap watchdog), the relay would otherwise stay open and the
        panel SSE would hang forever. This pushes an optional final ``error``
        event so the panel shows WHY the chat ended, then closes the stream.
        Returns the closed session ids.
        """
        closed: list[str] = []
        for session_id, session in list(self._sessions.items()):
            if session.agent_id != agent_id or session.closed:
                continue
            if error:
                session.queue.put_nowait({"kind": "error", "text": error})
            self.close(session_id)
            closed.append(session_id)
        return closed

    def park(self, session_id: str, task_id: str) -> bool:
        """Mark a session as parked awaiting board review of ``task_id``.

        Keeps the session alive (the opposite of ``close``): the intake agent
        stays resident with the full interview in context, so the board's
        feedback can be injected for an in-place re-draft. False if no such
        live session (it was already reaped / never opened).
        """
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return False
        session.task_id = task_id
        self.log.info(
            "Live intake session parked for board review",
            session_id=session_id,
            task_id=task_id,
        )
        return True

    def find_by_task(self, task_id: str) -> LiveIntakeSession | None:
        """Return the live (un-closed) session parked for ``task_id``, if any."""
        for session in self._sessions.values():
            if session.task_id == task_id and not session.closed:
                return session
        return None

    # -- agent -> panel ----------------------------------------------------

    def push(self, session_id: str, event: dict[str, Any]) -> bool:
        """Queue one agent event for the SSE stream. False if no such session."""
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return False
        session.last_activity = time.monotonic()
        session.queue.put_nowait(event)
        return True

    def idle_session_ids(self, threshold_seconds: float) -> list[tuple[str, str]]:
        """Return ``(session_id, agent_id)`` for live chats idle past the threshold.

        Idle = ``threshold_seconds`` with no turn (push/deliver). Closed and
        board-review-parked (``task_id`` set) sessions are excluded — the latter
        are intentionally kept alive while the board reviews. ``threshold <= 0``
        yields nothing (the caller treats that as "disabled").
        """
        if threshold_seconds <= 0:
            return []
        now = time.monotonic()
        return [
            (sid, s.agent_id)
            for sid, s in self._sessions.items()
            if not s.closed
            and s.task_id is None
            and now - s.last_activity > threshold_seconds
        ]

    @staticmethod
    async def _keepalive(session: LiveIntakeSession) -> None:
        """Refresh ``last_activity`` while a stream is connected (see ``stream``)."""
        while True:
            await asyncio.sleep(_STREAM_KEEPALIVE_SECONDS)
            session.last_activity = time.monotonic()

    async def stream(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield queued agent events until the session is closed.

        A connected stream keeps the session's ``last_activity`` fresh (a
        ``_keepalive`` task ticks every ``_STREAM_KEEPALIVE_SECONDS`` even with
        no events), so the idle reaper treats an open, actively-watched chat as
        alive — a human reading a proposed draft without typing is not "idle".
        The keepalive runs beside an un-cancelled ``queue.get()`` (so no live
        token/``_CLOSE`` is lost) and is cancelled when the stream ends; on
        client disconnect the refresh stops and an abandoned chat reaps normally.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        keepalive = asyncio.create_task(self._keepalive(session))
        try:
            while True:
                item = await session.queue.get()
                if item is _CLOSE:
                    return
                session.last_activity = time.monotonic()
                yield item
        finally:
            keepalive.cancel()

    # -- panel -> agent ----------------------------------------------------

    async def deliver(self, session_id: str, text: str) -> bool:
        """Deliver the human's message to the container's receiver. False if gone."""
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return False
        session.last_activity = time.monotonic()  # human turn = activity
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

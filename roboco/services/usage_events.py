"""
Usage Event Publisher

Throttled helpers for publishing USAGE_UPDATE and USAGE_SNAPSHOT events
to the StreamEventBus.  Consumed by the orchestrator token sweep and
forwarded to /ws/system WebSocket clients via the websocket_bridge.

Throttle window: one USAGE_UPDATE publish per agent per 5-second window.
Subsequent calls within the window are silently dropped so a frequent
sweep loop cannot flood the event bus or WebSocket clients.

USAGE_SNAPSHOT is always published (no per-agent throttle) — it is an
aggregate and published at most once per sweep cycle.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from roboco.events.stream_bus import StreamEventBus

_THROTTLE_WINDOW_SECONDS: float = 5.0


class _UsageThrottle:
    """Per-agent last-publish timestamp tracker.

    Uses ``time.monotonic()`` so clock adjustments (NTP, DST) don't cause
    spurious suppressions or double-fires.
    """

    def __init__(self, window: float = _THROTTLE_WINDOW_SECONDS) -> None:
        self._window = window
        self._last: dict[str, float] = {}

    def should_publish(self, agent_id: str) -> bool:
        """Return True if the agent is outside the throttle window.

        Also records the current monotonic time as the new *last published*
        timestamp when it returns True, so the caller does not need to call a
        separate ``record()`` method.
        """
        now = time.monotonic()
        if now - self._last.get(agent_id, 0.0) >= self._window:
            self._last[agent_id] = now
            return True
        return False


# Module-level singleton — shared across all callers in this process.
_throttle = _UsageThrottle()


@dataclass(frozen=True, slots=True)
class UsageUpdate:
    """Per-agent cumulative token counts carried by a USAGE_UPDATE event.

    Fields map directly onto the event payload the panel consumes (the
    backend emits these per active agent during the token sweep).
    """

    agent_id: str
    task_id: str | None
    input_tokens: int
    output_tokens: int
    model: str
    timestamp: datetime | None = None

    def event_data(self) -> dict[str, Any]:
        """Render the event payload, stamping ``timestamp`` if not supplied."""
        return {
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model": self.model,
            "timestamp": (self.timestamp or datetime.now(UTC)).isoformat(),
        }


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """Aggregate token/cost totals carried by a USAGE_SNAPSHOT event.

    ``totals`` is ``{"input_tokens": int, "output_tokens": int}``; ``by_agent``
    is the per-agent breakdown (each item carries ``agent_id``, token counts,
    ``model`` and ``cost_estimate``).
    """

    period: str
    totals: dict[str, int]
    cost_estimate: float
    by_agent: list[dict[str, Any]]
    timestamp: datetime | None = None

    def event_data(self) -> dict[str, Any]:
        """Render the event payload, stamping ``timestamp`` if not supplied."""
        return {
            "period": self.period,
            "totals": self.totals,
            "cost_estimate": self.cost_estimate,
            "by_agent": self.by_agent,
            "timestamp": (self.timestamp or datetime.now(UTC)).isoformat(),
        }


async def publish_usage_update(bus: StreamEventBus, update: UsageUpdate) -> bool:
    """Publish a USAGE_UPDATE event if the per-agent throttle window has elapsed.

    Returns True if the event was published; False if suppressed by the throttle.
    """
    if not _throttle.should_publish(update.agent_id):
        return False

    from roboco.models.events import Event, EventType  # lazy — avoids circular import

    await bus.publish(Event(type=EventType.USAGE_UPDATE, data=update.event_data()))
    return True


async def publish_usage_snapshot(bus: StreamEventBus, snapshot: UsageSnapshot) -> None:
    """Publish a USAGE_SNAPSHOT aggregate event (no throttle)."""
    from roboco.models.events import Event, EventType  # lazy — avoids circular import

    await bus.publish(Event(type=EventType.USAGE_SNAPSHOT, data=snapshot.event_data()))

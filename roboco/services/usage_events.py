"""
Usage Event Publisher

Helper for publishing USAGE_SNAPSHOT aggregate events to the StreamEventBus.
Consumed by the orchestrator token sweep and forwarded to /ws/system WebSocket
clients via the websocket_bridge.

USAGE_SNAPSHOT is an aggregate, published at most once per sweep cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from roboco.events.stream_bus import StreamEventBus


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


async def publish_usage_snapshot(bus: StreamEventBus, snapshot: UsageSnapshot) -> None:
    """Publish a USAGE_SNAPSHOT aggregate event (no throttle)."""
    from roboco.models.events import Event, EventType  # lazy — avoids circular import

    await bus.publish(Event(type=EventType.USAGE_SNAPSHOT, data=snapshot.event_data()))

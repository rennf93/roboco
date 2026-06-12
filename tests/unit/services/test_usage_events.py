"""Unit tests for roboco.services.usage_events.

Covers the publish_usage_snapshot helper.  No real Redis or event bus is
needed — we use AsyncMock to assert that bus.publish is called with the right
payload and type.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.usage_events import UsageSnapshot, publish_usage_snapshot

# ---------------------------------------------------------------------------
# publish_usage_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_usage_snapshot_always_publishes() -> None:
    """publish_usage_snapshot has no throttle — always publishes."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    expected_input = 500
    expected_cost = 0.0025
    expected_agents = 2

    await publish_usage_snapshot(
        bus,
        UsageSnapshot(
            period="live",
            totals={"input_tokens": expected_input, "output_tokens": 200},
            cost_estimate=expected_cost,
            by_agent=[
                {
                    "agent_id": "be-dev-1",
                    "input_tokens": 300,
                    "output_tokens": 100,
                    "model": "sonnet",
                    "cost_estimate": 0.0015,
                },
                {
                    "agent_id": "be-dev-2",
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "model": "sonnet",
                    "cost_estimate": 0.0010,
                },
            ],
        ),
    )

    bus.publish.assert_awaited_once()
    event = bus.publish.await_args.args[0]
    assert event.type.value == "usage.snapshot"
    assert event.data["period"] == "live"
    assert event.data["totals"]["input_tokens"] == expected_input
    assert event.data["cost_estimate"] == expected_cost
    assert len(event.data["by_agent"]) == expected_agents
    assert "timestamp" in event.data


@pytest.mark.asyncio
async def test_publish_usage_snapshot_twice_both_published() -> None:
    """No throttle on snapshot: two rapid calls both publish."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    expected_calls = 2

    for _ in range(expected_calls):
        await publish_usage_snapshot(
            bus,
            UsageSnapshot(
                period="live",
                totals={"input_tokens": 0, "output_tokens": 0},
                cost_estimate=0.0,
                by_agent=[],
            ),
        )

    assert bus.publish.await_count == expected_calls

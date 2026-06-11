"""Unit tests for roboco.services.usage_events.

Covers the _UsageThrottle class and the publish_usage_update /
publish_usage_snapshot helpers.  No real Redis or event bus is needed —
we use AsyncMock to assert that bus.publish is called with the right
payload and type.

The throttle suppression test is the acceptance-criterion gate:
  "Server-side throttle prevents more than 1 USAGE_UPDATE publish per
   agent per 5-second window."
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.usage_events import (
    UsageSnapshot,
    UsageUpdate,
    _UsageThrottle,
    publish_usage_snapshot,
    publish_usage_update,
)

# ---------------------------------------------------------------------------
# _UsageThrottle
# ---------------------------------------------------------------------------


def test_throttle_allows_first_publish() -> None:
    """A fresh agent has no prior timestamp — first publish is always allowed."""
    th = _UsageThrottle(window=5.0)
    assert th.should_publish("be-dev-1") is True


def test_throttle_suppresses_second_publish_within_window() -> None:
    """Second call within the 5-second window returns False (suppressed)."""
    th = _UsageThrottle(window=5.0)

    with patch("roboco.services.usage_events.time") as mock_time:
        mock_time.monotonic.return_value = 100.0
        assert th.should_publish("be-dev-1") is True  # first → allowed

        mock_time.monotonic.return_value = 104.9  # 4.9 s later — still inside window
        assert th.should_publish("be-dev-1") is False  # suppressed


def test_throttle_allows_publish_after_window_expires() -> None:
    """After the full window elapses, the next publish is allowed again."""
    th = _UsageThrottle(window=5.0)

    with patch("roboco.services.usage_events.time") as mock_time:
        mock_time.monotonic.return_value = 100.0
        assert th.should_publish("be-dev-1") is True  # first

        mock_time.monotonic.return_value = 105.0  # exactly 5 s later
        assert th.should_publish("be-dev-1") is True  # window elapsed → allowed


def test_throttle_tracks_agents_independently() -> None:
    """Different agents have independent throttle windows."""
    th = _UsageThrottle(window=5.0)

    with patch("roboco.services.usage_events.time") as mock_time:
        mock_time.monotonic.return_value = 100.0

        assert th.should_publish("be-dev-1") is True
        # be-dev-2 has never published, so it is always allowed.
        assert th.should_publish("be-dev-2") is True

        mock_time.monotonic.return_value = 101.0
        # be-dev-1 is suppressed; be-dev-2 is also now suppressed.
        assert th.should_publish("be-dev-1") is False
        assert th.should_publish("be-dev-2") is False


def test_throttle_records_timestamp_on_allow() -> None:
    """should_publish records the current time when it returns True."""
    th = _UsageThrottle(window=5.0)
    recorded_at = 200.0

    with patch("roboco.services.usage_events.time") as mock_time:
        mock_time.monotonic.return_value = recorded_at
        th.should_publish("be-dev-1")
        assert th._last["be-dev-1"] == recorded_at


# ---------------------------------------------------------------------------
# publish_usage_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_usage_update_calls_bus_publish() -> None:
    """First call in a window publishes the event and returns True."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    th = _UsageThrottle(window=5.0)
    expected_input = 100
    expected_output = 50

    with patch("roboco.services.usage_events._throttle", th):
        result = await publish_usage_update(
            bus,
            UsageUpdate(
                agent_id="be-dev-1",
                task_id="task-abc",
                input_tokens=expected_input,
                output_tokens=expected_output,
                model="claude-sonnet-4-6",
            ),
        )

    assert result is True
    bus.publish.assert_awaited_once()
    event = bus.publish.await_args.args[0]
    assert event.type.value == "usage.update"
    assert event.data["agent_id"] == "be-dev-1"
    assert event.data["task_id"] == "task-abc"
    assert event.data["input_tokens"] == expected_input
    assert event.data["output_tokens"] == expected_output
    assert event.data["model"] == "claude-sonnet-4-6"
    assert "timestamp" in event.data


@pytest.mark.asyncio
async def test_publish_usage_update_throttle_suppresses_second_call() -> None:
    """Second publish within the throttle window is suppressed (returns False)."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    th = _UsageThrottle(window=5.0)

    with (
        patch("roboco.services.usage_events._throttle", th),
        patch("roboco.services.usage_events.time") as mock_time,
    ):
        mock_time.monotonic.return_value = 100.0
        first = await publish_usage_update(
            bus,
            UsageUpdate(
                agent_id="be-dev-1",
                task_id=None,
                input_tokens=10,
                output_tokens=5,
                model="sonnet",
            ),
        )

        mock_time.monotonic.return_value = 102.0  # 2 s later — still suppressed
        second = await publish_usage_update(
            bus,
            UsageUpdate(
                agent_id="be-dev-1",
                task_id=None,
                input_tokens=20,
                output_tokens=10,
                model="sonnet",
            ),
        )

    assert first is True
    assert second is False
    # bus.publish should only have been called once.
    assert bus.publish.await_count == 1


@pytest.mark.asyncio
async def test_publish_usage_update_custom_timestamp() -> None:
    """Custom timestamp is passed through to the event data."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    ts = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)

    # Use a fresh throttle so the first publish goes through.
    th = _UsageThrottle(window=5.0)
    with patch("roboco.services.usage_events._throttle", th):
        await publish_usage_update(
            bus,
            UsageUpdate(
                agent_id="be-dev-1",
                task_id=None,
                input_tokens=0,
                output_tokens=0,
                model="sonnet",
                timestamp=ts,
            ),
        )

    event = bus.publish.await_args.args[0]
    assert event.data["timestamp"] == ts.isoformat()


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

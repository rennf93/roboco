"""Coverage for roboco.events.bus thin wrapper functions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.events.bus import EventBus, get_event_bus, init_event_bus
from roboco.events.stream_bus import StreamEventBus


def test_get_event_bus_delegates() -> None:
    """get_event_bus() returns the underlying stream event bus singleton."""
    fake_bus = MagicMock()
    with patch(
        "roboco.events.bus.get_stream_event_bus", return_value=fake_bus
    ) as mock_get:
        result = get_event_bus()
    mock_get.assert_called_once()
    assert result is fake_bus


@pytest.mark.asyncio
async def test_init_event_bus_delegates() -> None:
    """init_event_bus() forwards args to init_stream_event_bus (line 53)."""
    fake_bus = MagicMock()
    with patch(
        "roboco.events.bus.init_stream_event_bus",
        new_callable=AsyncMock,
        return_value=fake_bus,
    ) as mock_init:
        result = await init_event_bus(consumer_name="custom", recover_pending=False)
    mock_init.assert_awaited_once_with(consumer_name="custom", recover_pending=False)
    assert result is fake_bus


def test_event_bus_alias_is_stream_event_bus() -> None:
    """EventBus is an alias for StreamEventBus."""

    assert EventBus is StreamEventBus

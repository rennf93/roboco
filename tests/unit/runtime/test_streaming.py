"""runtime.streaming coverage."""

from __future__ import annotations

import pytest
from roboco.runtime.streaming import (
    get_reasoning_stream_callback,
    set_reasoning_stream_callback,
    stream_reasoning,
)


@pytest.fixture(autouse=True)
def reset_callback():
    """Reset the global callback after each test."""
    yield
    set_reasoning_stream_callback(None)


def test_get_callback_returns_none_initially() -> None:
    set_reasoning_stream_callback(None)
    assert get_reasoning_stream_callback() is None


def test_set_and_get_callback() -> None:
    async def cb(agent_id, chunk, metadata):
        pass

    set_reasoning_stream_callback(cb)
    assert get_reasoning_stream_callback() is cb


@pytest.mark.asyncio
async def test_stream_reasoning_calls_callback() -> None:
    received: list[tuple[str, str, dict]] = []

    async def cb(agent_id: str, chunk: str, metadata: dict) -> None:
        received.append((agent_id, chunk, metadata))

    set_reasoning_stream_callback(cb)
    await stream_reasoning("be-dev-1", "thinking...", {"step": 1})
    assert received == [("be-dev-1", "thinking...", {"step": 1})]


@pytest.mark.asyncio
async def test_stream_reasoning_no_callback_silent() -> None:
    set_reasoning_stream_callback(None)
    # No raise.
    await stream_reasoning("be-dev-1", "chunk")


@pytest.mark.asyncio
async def test_stream_reasoning_default_metadata_is_empty_dict() -> None:
    received: list[tuple[str, str, dict]] = []

    async def cb(agent_id: str, chunk: str, metadata: dict) -> None:
        received.append((agent_id, chunk, metadata))

    set_reasoning_stream_callback(cb)
    await stream_reasoning("be-dev-1", "chunk")
    assert received[0][2] == {}

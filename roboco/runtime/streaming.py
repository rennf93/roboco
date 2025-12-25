"""
Agent Reasoning Stream Callback

Allows external systems (like WebSocket handlers) to receive agent reasoning
as it happens. Used for real-time UI updates.
"""

from collections.abc import Awaitable, Callable
from typing import Any

# Type for reasoning stream callback
# Called with (agent_id: str, chunk: str, metadata: dict)
ReasoningStreamCallback = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class _CallbackHolder:
    """Holder for the global reasoning stream callback."""

    callback: ReasoningStreamCallback | None = None


def set_reasoning_stream_callback(callback: ReasoningStreamCallback | None) -> None:
    """
    Set the global callback for agent reasoning streams.

    This is called during bootstrap to wire up WebSocket broadcasting.

    Args:
        callback: Async function that receives (agent_id, chunk, metadata)
    """
    _CallbackHolder.callback = callback


def get_reasoning_stream_callback() -> ReasoningStreamCallback | None:
    """Get the current reasoning stream callback."""
    return _CallbackHolder.callback


async def stream_reasoning(
    agent_id: str,
    chunk: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Stream a reasoning chunk to the registered callback.

    Args:
        agent_id: The agent producing the reasoning
        chunk: The text chunk to stream
        metadata: Optional metadata about the chunk
    """
    if _CallbackHolder.callback is not None:
        await _CallbackHolder.callback(agent_id, chunk, metadata or {})

"""Structural type for the composed StreamEventBus, TYPE_CHECKING-only.

``StreamEventBus`` is composed from mixins (``roboco/events/stream_bus.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin against its own
bases only, so a call into a sibling mixin, or into StreamEventBus's own members, would
not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``StreamEventBusSelf``, which
declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the other
classes (``StreamEventBusSurface`` what ``StreamEventBus`` itself does), with each
method's own decorators, parameters and return type, so no moved body changes.
``tests/unit/events/test_stream_event_bus_mixins.py`` fails on a stub its surface's
class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import asyncio

    import redis.asyncio as redis

    from roboco.events.stream_bus import EventHandler
    from roboco.models.events import EventType


class StreamConsumerSurface(Protocol):
    """``StreamConsumer``'s members that other classes reach."""

    @staticmethod
    def _decode_event_data(data: dict) -> str | None: ...
    async def _handle_message(
        self, stream: str, message_id: str, data: dict
    ) -> None: ...
    @staticmethod
    def _to_str(value: object) -> str: ...


class StreamEventBusSurface(Protocol):
    """``StreamEventBus``'s members that other classes reach."""

    DEAD_LETTER_STREAM: str
    MAX_DELIVERY_COUNT: int
    MAX_STREAM_LENGTH: int

    async def _ensure_consumer_group(self, stream: str) -> None: ...
    def _get_all_stream_names(self) -> list[str]: ...


class StreamRecoverySurface(Protocol):
    """``StreamRecovery``'s members that other classes reach."""

    async def _dead_letter(
        self, stream: str, message_id: str, data: dict, reason: str
    ) -> None: ...
    async def recover_pending(self, idle_time_ms: int = ...) -> int: ...


class StreamEventBusSelf(
    StreamConsumerSurface, StreamEventBusSurface, StreamRecoverySurface, Protocol
):
    """Every cross-class member a moved method body reaches via ``self``:
    StreamEventBus's instance attributes here, each class's
    members on its surface."""

    _handlers: dict[EventType, list[EventHandler]]
    _listen_task: asyncio.Task | None
    _reclaim_interval: int
    _reclaim_task: asyncio.Task | None
    _redis: redis.Redis | None
    _running: bool
    consumer_name: str
    group_name: str
    redis_url: str

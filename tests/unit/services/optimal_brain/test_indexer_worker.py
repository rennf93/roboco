"""Unit tests for the indexer worker: enqueue/consume round trip, backlog
shed rule, and the inline fallback used when the stream is down/disabled.

No test uses a real Redis (see tests/conftest.py's ``_no_live_redis``); a
minimal hand-rolled fake stands in for the redis-py stream surface, matching
the convention in tests/unit/events/test_bus.py.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.events.stream_bus import StreamEventBus
from roboco.models.events import Event, EventType
from roboco.services.optimal_brain import indexer_worker

if TYPE_CHECKING:
    from redis.asyncio import Redis


class _FakeStreamRedis:
    """In-memory stand-in for the XADD/XINFO GROUPS/XPENDING/XRANGE/XDEL
    surface used here."""

    def __init__(self) -> None:
        self.xadd_calls: list[tuple[str, dict]] = []
        self.xdel_calls: list[tuple[str, tuple[str, ...]]] = []
        self.xrange_calls: list[tuple[str, str]] = []
        # Consumer-group lag (XINFO GROUPS): None simulates Redis <7 (field
        # absent), forcing the XPENDING fallback.
        self.lag: int | None = 0
        self.last_delivered_id = "0-0"
        self.pending_value = 0
        self.xrange_value: list[tuple[bytes, dict[bytes, bytes]]] = []

    async def xadd(
        self,
        stream: str,
        fields: dict,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> bytes:
        del maxlen, approximate
        self.xadd_calls.append((stream, dict(fields)))
        return b"1-0"

    async def xinfo_groups(self, stream: str) -> list[dict]:
        del stream
        if self.lag is None:
            return []
        return [
            {
                b"name": indexer_worker.CONSUMER_GROUP.encode(),
                b"lag": self.lag,
                b"last-delivered-id": self.last_delivered_id.encode(),
            }
        ]

    async def xpending(self, stream: str, group: str) -> dict:
        del stream, group
        return {"pending": self.pending_value}

    async def xrange(
        self, stream: str, min: str = "-", max: str = "+", count: int | None = None
    ) -> list:
        del stream, count
        self.xrange_calls.append((min, max))
        return self.xrange_value

    async def xdel(self, stream: str, *ids: str) -> int:
        self.xdel_calls.append((stream, ids))
        return len(ids)


def _event_bytes(kind: str, payload: dict | None = None) -> bytes:
    return (
        Event(
            type=EventType.INDEX_REQUESTED,
            data={"kind": kind, "payload": payload or {}},
        )
        .to_json()
        .encode()
    )


@pytest.fixture(autouse=True)
def _reset_bus_holder() -> Any:
    """Each test gets its own indexer bus, the module holds a singleton."""
    indexer_worker._IndexerBusHolder.instance = None
    yield
    indexer_worker._IndexerBusHolder.instance = None


# ---------------------------------------------------------------------------
# enqueue -> consume round trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_publishes_event_to_index_stream() -> None:
    """enqueue_index_request publishes a real Event through StreamEventBus.publish
    (not a mock) onto roboco:stream:index, kind/payload intact."""
    bus = StreamEventBus(group_name=indexer_worker.CONSUMER_GROUP)
    fake = _FakeStreamRedis()
    bus._redis = cast("Redis", fake)  # already "connected"
    indexer_worker._IndexerBusHolder.instance = bus

    entry_id = str(uuid4())
    await indexer_worker.enqueue_index_request(
        "journal_entry",
        {"entry_id": entry_id, "content": "hi", "entry_type": "general"},
    )

    assert len(fake.xadd_calls) == 1
    stream, fields = fake.xadd_calls[0]
    assert stream == indexer_worker.INDEX_STREAM
    assert fields["type"] == EventType.INDEX_REQUESTED.value

    # Round trip: decode exactly as the consumer side would.
    event = Event.from_json(fields["data"])
    assert event.data["kind"] == "journal_entry"
    assert event.data["payload"]["entry_id"] == entry_id


@pytest.mark.asyncio
async def test_handle_index_event_dispatches_journal_entry_with_real_uuids() -> None:
    """The consumer-side handler reconstructs UUID fields from the JSON-safe
    payload and calls the matching OptimalService method."""
    entry_id = uuid4()
    agent_id = uuid4()
    event = Event(
        type=EventType.INDEX_REQUESTED,
        data={
            "kind": "journal_entry",
            "payload": {
                "content": "lesson",
                "entry_type": "general",
                "entry_id": str(entry_id),
                "agent_id": str(agent_id),
                "task_id": None,
                "tags": ["t"],
            },
        },
    )

    fake_optimal = MagicMock()
    fake_optimal.index_journal_entry = AsyncMock(return_value=None)
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    ):
        await indexer_worker._handle_index_event(event)

    fake_optimal.index_journal_entry.assert_awaited_once()
    params = fake_optimal.index_journal_entry.await_args.args[0]
    assert params.entry_id == entry_id
    assert params.agent_id == agent_id
    assert params.task_id is None


@pytest.mark.asyncio
async def test_handle_index_event_dispatches_documentation() -> None:
    event = Event(
        type=EventType.INDEX_REQUESTED,
        data={
            "kind": "documentation",
            "payload": {
                "sources": ["a.md"],
                "project": "roboco",
                "provenance": "live_write",
                "task_id": "t1",
            },
        },
    )
    fake_optimal = MagicMock()
    fake_optimal.index_documentation = AsyncMock(return_value=1)
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    ):
        await indexer_worker._handle_index_event(event)

    fake_optimal.index_documentation.assert_awaited_once_with(
        ["a.md"], project="roboco", provenance="live_write", task_id="t1"
    )


@pytest.mark.asyncio
async def test_handle_index_event_dispatches_journal_unindex() -> None:
    entry_id = uuid4()
    event = Event(
        type=EventType.INDEX_REQUESTED,
        data={"kind": "journal_unindex", "payload": {"entry_id": str(entry_id)}},
    )
    fake_optimal = MagicMock()
    fake_optimal.unindex_journal_entry = AsyncMock(return_value=None)
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    ):
        await indexer_worker._handle_index_event(event)

    fake_optimal.unindex_journal_entry.assert_awaited_once_with(entry_id)


@pytest.mark.asyncio
async def test_handle_index_event_dispatches_learning() -> None:
    event = Event(
        type=EventType.INDEX_REQUESTED,
        data={
            "kind": "learning",
            "payload": {
                "content": "lesson",
                "category": "journal_learning",
                "agent_id": None,
                "task_id": None,
                "shareable": True,
                "tags": [],
            },
        },
    )
    fake_optimal = MagicMock()
    fake_optimal.record_learning = AsyncMock(return_value="lrn-1")
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    ):
        await indexer_worker._handle_index_event(event)

    fake_optimal.record_learning.assert_awaited_once()
    params = fake_optimal.record_learning.await_args.args[0]
    assert params.category == "journal_learning"
    assert params.shareable is True


# ---------------------------------------------------------------------------
# inline fallback: stream disabled or Redis unreachable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_falls_back_inline_when_stream_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(indexer_worker.settings, "indexer_stream_enabled", False)
    fake_optimal = MagicMock()
    fake_optimal.unindex_journal_entry = AsyncMock(return_value=None)
    entry_id = uuid4()

    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    ):
        await indexer_worker.enqueue_index_request(
            "journal_unindex", {"entry_id": str(entry_id)}
        )
        await indexer_worker.drain_inline_index_tasks()

    fake_optimal.unindex_journal_entry.assert_awaited_once_with(entry_id)


@pytest.mark.asyncio
async def test_enqueue_falls_back_inline_when_publish_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis unreachable (publish raises): never bubbles to the caller,
    the index still runs, just inline."""
    monkeypatch.setattr(indexer_worker.settings, "indexer_stream_enabled", True)
    bus = StreamEventBus(group_name=indexer_worker.CONSUMER_GROUP)

    async def _boom() -> None:
        raise ConnectionError("no redis")

    monkeypatch.setattr(bus, "connect", _boom)
    indexer_worker._IndexerBusHolder.instance = bus

    fake_optimal = MagicMock()
    fake_optimal.unindex_journal_entry = AsyncMock(return_value=None)
    entry_id = uuid4()

    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    ):
        await indexer_worker.enqueue_index_request(
            "journal_unindex", {"entry_id": str(entry_id)}
        )
        await indexer_worker.drain_inline_index_tasks()

    fake_optimal.unindex_journal_entry.assert_awaited_once_with(entry_id)


@pytest.mark.asyncio
async def test_inline_fallback_dead_letters_journal_entry_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A journal_entry inline-fallback failure is persisted to the
    rag_index_failures dead-letter, same as the pre-stream behavior."""
    monkeypatch.setattr(indexer_worker.settings, "indexer_stream_enabled", False)
    fake_optimal = MagicMock()
    fake_optimal.index_journal_entry = AsyncMock(side_effect=RuntimeError("ollama 429"))
    entry_id = uuid4()
    payload: dict[str, Any] = {
        "content": "lesson",
        "entry_type": "general",
        "entry_id": str(entry_id),
        "agent_id": None,
        "task_id": None,
        "tags": [],
    }

    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=fake_optimal),
        ),
        patch(
            "roboco.services.rag_index_failures.persist_failure", new=AsyncMock()
        ) as mock_persist,
    ):
        await indexer_worker.enqueue_index_request("journal_entry", payload)
        await indexer_worker.drain_inline_index_tasks()

    mock_persist.assert_awaited_once()
    assert mock_persist.await_args is not None
    args = mock_persist.await_args.args
    assert args[0] == "journal_entry"
    assert args[1]["entry_id"] == str(entry_id)


@pytest.mark.asyncio
async def test_inline_fallback_documentation_failure_only_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-journal kinds are best-effort logged on inline failure, no
    dead-letter table write (matches the pre-stream _index_docs_background
    behavior, which only ever logged)."""
    monkeypatch.setattr(indexer_worker.settings, "indexer_stream_enabled", False)
    fake_optimal = MagicMock()
    fake_optimal.index_documentation = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=fake_optimal),
        ),
        patch(
            "roboco.services.rag_index_failures.persist_failure", new=AsyncMock()
        ) as mock_persist,
    ):
        await indexer_worker.enqueue_index_request(
            "documentation", {"sources": ["a.md"]}
        )
        await indexer_worker.drain_inline_index_tasks()

    mock_persist.assert_not_awaited()


# ---------------------------------------------------------------------------
# backlog shed rule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backlog_shed_never_drops_journal_unindex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(indexer_worker.settings, "indexer_max_backlog", 2)
    bus = StreamEventBus(group_name=indexer_worker.CONSUMER_GROUP)
    fake = _FakeStreamRedis()
    bus._redis = cast("Redis", fake)
    fake.lag = 5  # 5 - cap(2) = 3 excess
    fake.xrange_value = [
        (b"1-0", {b"data": _event_bytes("journal_unindex")}),
        (b"2-0", {b"data": _event_bytes("journal_entry")}),
        (b"3-0", {b"data": _event_bytes("documentation")}),
    ]

    await indexer_worker._check_backlog(bus)

    dead_letters = [
        c for c in fake.xadd_calls if c[0] == StreamEventBus.DEAD_LETTER_STREAM
    ]
    # journal_unindex (1-0) is never shed; the other two are.
    assert len(dead_letters) == len(fake.xrange_value) - 1
    assert all(f["reason"] == "backlog_shed" for _s, f in dead_letters)
    assert fake.xdel_calls == [
        (indexer_worker.INDEX_STREAM, ("2-0",)),
        (indexer_worker.INDEX_STREAM, ("3-0",)),
    ]


@pytest.mark.asyncio
async def test_backlog_ignores_acked_history_uses_group_lag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """600 entries can sit in the stream (nothing XTRIMs/XDELs an acked one)
    while the consumer group's lag is 0 -- everything has already been
    delivered and acked. XLEN would see 600 and shed forever; lag correctly
    sees no backlog at all."""
    monkeypatch.setattr(indexer_worker.settings, "indexer_max_backlog", 500)
    bus = StreamEventBus(group_name=indexer_worker.CONSUMER_GROUP)
    fake = _FakeStreamRedis()
    bus._redis = cast("Redis", fake)
    fake.lag = 0

    await indexer_worker._check_backlog(bus)

    assert fake.xadd_calls == []
    assert fake.xdel_calls == []


@pytest.mark.asyncio
async def test_backlog_sheds_oldest_undelivered_past_lag_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lag of 600 against a cap of 500 sheds exactly the excess, read from
    strictly after the group's last-delivered-id so an already
    delivered/acked entry is never a shed candidate."""
    cap = 500
    lag = 600
    excess = lag - cap
    monkeypatch.setattr(indexer_worker.settings, "indexer_max_backlog", cap)
    bus = StreamEventBus(group_name=indexer_worker.CONSUMER_GROUP)
    fake = _FakeStreamRedis()
    bus._redis = cast("Redis", fake)
    fake.lag = lag
    fake.last_delivered_id = "42-0"
    fake.xrange_value = [
        (f"{100 + i}-0".encode(), {b"data": _event_bytes("documentation")})
        for i in range(excess)
    ]

    await indexer_worker._check_backlog(bus)

    assert fake.xrange_calls == [("(42-0", "+")]
    dead_letters = [
        c for c in fake.xadd_calls if c[0] == StreamEventBus.DEAD_LETTER_STREAM
    ]
    assert len(dead_letters) == excess
    assert len(fake.xdel_calls) == excess


@pytest.mark.asyncio
async def test_backlog_falls_back_to_xpending_when_lag_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis <7 has no 'lag' field on XINFO GROUPS; fall back to XPENDING's
    still-unacked count."""
    monkeypatch.setattr(indexer_worker.settings, "indexer_max_backlog", 10)
    bus = StreamEventBus(group_name=indexer_worker.CONSUMER_GROUP)
    fake = _FakeStreamRedis()
    bus._redis = cast("Redis", fake)
    fake.lag = None
    fake.pending_value = 5  # under cap(10): no shed

    await indexer_worker._check_backlog(bus)

    assert fake.xadd_calls == []
    assert fake.xdel_calls == []


# ---------------------------------------------------------------------------
# indexer's own embedder: lower concurrency/batch than the shared default,
# so it never saturates ollama and starves a concurrent query embed
# ---------------------------------------------------------------------------


def test_build_indexer_embedder_uses_indexer_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(indexer_worker.settings, "indexer_embed_concurrency", 1)
    monkeypatch.setattr(indexer_worker.settings, "indexer_embed_batch_size", 8)

    embedder = indexer_worker._build_indexer_embedder()

    assert embedder.max_concurrent == indexer_worker.settings.indexer_embed_concurrency
    assert embedder.batch_size == indexer_worker.settings.indexer_embed_batch_size


# ---------------------------------------------------------------------------
# run_indexer: wiring, and the dedicated_embedder gate (item 5)
# ---------------------------------------------------------------------------


def _run_indexer_bus() -> tuple[StreamEventBus, _FakeStreamRedis]:
    bus = StreamEventBus(group_name=indexer_worker.CONSUMER_GROUP)
    fake = _FakeStreamRedis()
    bus._redis = cast("Redis", fake)
    indexer_worker._IndexerBusHolder.instance = bus
    return bus, fake


@pytest.mark.asyncio
async def test_run_indexer_subscribes_starts_listening_and_backlog_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_indexer wires the INDEX_REQUESTED handler onto the consumer-group
    bus, starts its listen loop, and kicks off the periodic backlog-shed
    loop."""
    bus, _fake = _run_indexer_bus()
    start_listening_mock = AsyncMock()
    monkeypatch.setattr(bus, "start_listening", start_listening_mock)
    monkeypatch.setattr(bus, "disconnect", AsyncMock())
    backlog_mock = AsyncMock()
    monkeypatch.setattr(indexer_worker, "_backlog_loop", backlog_mock)

    fake_optimal = MagicMock()
    fake_optimal.ensure_periodic_update_running = AsyncMock()

    stop = asyncio.Event()
    stop.set()  # run_indexer returns as soon as it reaches stop.wait()

    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    ):
        await indexer_worker.run_indexer(stop)

    assert indexer_worker._handle_index_event in bus._handlers.get(
        EventType.INDEX_REQUESTED, []
    )
    start_listening_mock.assert_awaited_once()
    backlog_mock.assert_called_once_with(bus)
    fake_optimal.ensure_periodic_update_running.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_indexer_in_process_never_touches_shared_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dedicated_embedder=False (ROBOCO_ROLE=all, in-process) must never seed
    the shared-embedder singleton -- that process also serves interactive
    query embeds through the same singleton, and seeding it here would
    throttle every one of those too."""
    bus, _fake = _run_indexer_bus()
    monkeypatch.setattr(bus, "start_listening", AsyncMock())
    monkeypatch.setattr(bus, "disconnect", AsyncMock())
    monkeypatch.setattr(indexer_worker, "_backlog_loop", AsyncMock())

    fake_optimal = MagicMock()
    fake_optimal.ensure_periodic_update_running = AsyncMock()
    set_shared_mock = MagicMock()
    monkeypatch.setattr(
        "roboco.services.optimal_brain.shared_embedder.set_shared_embedder",
        set_shared_mock,
    )

    stop = asyncio.Event()
    stop.set()

    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    ):
        await indexer_worker.run_indexer(stop, dedicated_embedder=False)

    set_shared_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_indexer_dedicated_embedder_seeds_shared_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dedicated_embedder=True (the standalone ROBOCO_ROLE=indexer process)
    seeds the shared-embedder singleton with the indexer's own
    low-concurrency instance."""
    bus, _fake = _run_indexer_bus()
    monkeypatch.setattr(bus, "start_listening", AsyncMock())
    monkeypatch.setattr(bus, "disconnect", AsyncMock())
    monkeypatch.setattr(indexer_worker, "_backlog_loop", AsyncMock())

    fake_optimal = MagicMock()
    fake_optimal.ensure_periodic_update_running = AsyncMock()
    sentinel = object()
    monkeypatch.setattr(
        indexer_worker, "_build_indexer_embedder", MagicMock(return_value=sentinel)
    )
    set_shared_mock = MagicMock()
    monkeypatch.setattr(
        "roboco.services.optimal_brain.shared_embedder.set_shared_embedder",
        set_shared_mock,
    )

    stop = asyncio.Event()
    stop.set()

    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    ):
        await indexer_worker.run_indexer(stop, dedicated_embedder=True)

    set_shared_mock.assert_called_once_with(sentinel)


# ---------------------------------------------------------------------------
# run_indexer: boot-time RAG reconcile ownership per role
# ---------------------------------------------------------------------------


def _reconcile_probe(
    monkeypatch: pytest.MonkeyPatch, role: str
) -> tuple[asyncio.Event, MagicMock, MagicMock]:
    """A run_indexer setup with the bus and backlog loop stubbed, the role
    pinned, and schedule_rag_reconcile replaced by a recording mock."""
    bus, _fake = _run_indexer_bus()
    monkeypatch.setattr(bus, "start_listening", AsyncMock())
    monkeypatch.setattr(bus, "disconnect", AsyncMock())
    monkeypatch.setattr(indexer_worker, "_backlog_loop", AsyncMock())
    monkeypatch.setattr(indexer_worker.settings, "role", role)
    schedule_mock = MagicMock(return_value=None)
    monkeypatch.setattr(indexer_worker, "schedule_rag_reconcile", schedule_mock)
    fake_optimal = MagicMock()
    fake_optimal.ensure_periodic_update_running = AsyncMock()
    stop = asyncio.Event()
    stop.set()
    return stop, schedule_mock, fake_optimal


@pytest.mark.asyncio
async def test_run_indexer_owns_the_boot_reconcile_under_indexer_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROBOCO_ROLE=indexer never runs the FastAPI lifespan that schedules the
    boot-time RAG reconcile for the single-process role, so run_indexer must
    schedule it itself (2026-09-06: after the role split the zero-chunk
    backfill ran nowhere)."""
    stop, schedule_mock, fake_optimal = _reconcile_probe(monkeypatch, "indexer")

    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    ):
        await indexer_worker.run_indexer(stop)

    schedule_mock.assert_called_once_with(fake_optimal)


@pytest.mark.asyncio
async def test_run_indexer_leaves_the_boot_reconcile_to_the_lifespan_under_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under ROBOCO_ROLE=all the lifespan schedules the reconcile; the
    in-process run_indexer must not schedule a second copy."""
    stop, schedule_mock, fake_optimal = _reconcile_probe(monkeypatch, "all")

    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    ):
        await indexer_worker.run_indexer(stop)

    schedule_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_indexer_cancels_a_still_running_boot_reconcile_on_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reconcile still backfilling when the worker is stopped is cancelled,
    not left running past the bus it indexes through."""
    bus, _fake = _run_indexer_bus()
    monkeypatch.setattr(bus, "start_listening", AsyncMock())
    monkeypatch.setattr(bus, "disconnect", AsyncMock())
    monkeypatch.setattr(indexer_worker, "_backlog_loop", AsyncMock())
    monkeypatch.setattr(indexer_worker.settings, "role", "indexer")
    started = asyncio.Event()

    async def _slow_reconcile(_optimal: object) -> None:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(indexer_worker, "reconcile_rag_indexes", _slow_reconcile)
    scheduled: list[asyncio.Task[None]] = []
    real_schedule = indexer_worker.schedule_rag_reconcile

    def _capture(optimal: Any) -> asyncio.Task[None]:
        task = real_schedule(optimal)
        scheduled.append(task)
        return task

    monkeypatch.setattr(indexer_worker, "schedule_rag_reconcile", _capture)
    fake_optimal = MagicMock()
    fake_optimal.ensure_periodic_update_running = AsyncMock()
    stop = asyncio.Event()

    async def _stop_once_started() -> None:
        await started.wait()
        stop.set()

    stopper = asyncio.create_task(_stop_once_started())
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    ):
        await indexer_worker.run_indexer(stop)
    await stopper

    with contextlib.suppress(asyncio.CancelledError):
        await scheduled[0]
    assert scheduled[0].cancelled()


@pytest.mark.asyncio
async def test_reconcile_rag_indexes_is_a_noop_without_rag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RAG disabled (no OptimalService) skips every pass."""
    reclaim_mock = AsyncMock()
    monkeypatch.setattr(indexer_worker, "_reclaim_rag_index_failures", reclaim_mock)

    await indexer_worker.reconcile_rag_indexes(None)

    reclaim_mock.assert_not_awaited()

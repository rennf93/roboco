"""Indexer worker: moves embedding/RAG-index writes off the request path.

Measured on the production NAS: ollama (qwen3-embedding:0.6b, CPU) runs at
200% CPU, one ``/api/embed`` call takes 40-55s, and "Parallel embedding
starting" fires ~4x/minute inside the single backend process (journal
entries, doc writes, ``i_am_idle``). That's CPU + event-loop time stolen
from the API/dispatcher process, not request latency, since indexing was
already backgrounded (``asyncio.create_task``); it just ran in the wrong
process.

Producers (``journal.py``, ``task.py``) call :func:`enqueue_index_request`
instead of awaiting ``OptimalService.index_*``/``record_learning`` inline.
The request rides the Redis stream ``roboco:stream:index`` (via the shared
:class:`StreamEventBus`, category "index") to a dedicated
``ROBOCO_ROLE=indexer`` process running :func:`run_indexer`, which owns all
embedding work including the periodic RAG re-index sweep. When the stream is
disabled or Redis is unreachable, ``enqueue_index_request`` runs the index
inline instead (fire-and-forget, same posture as before), so a single-process
``ROBOCO_ROLE=all`` deployment with no separate consumer never loses
indexing. :func:`run_indexer` also runs as an in-process task under
``ROBOCO_ROLE=all`` (publish and consume in the same process); pass
``dedicated_embedder=True`` only for the standalone ``indexer`` process,
never for ``all``, see :func:`run_indexer`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import structlog

from roboco.config import settings
from roboco.events.stream_bus import StreamEventBus
from roboco.models.events import Event, EventType

if TYPE_CHECKING:
    from roboco.services.optimal import OptimalService
    from roboco.services.optimal_brain.ollama_embedder import OllamaEmbedder

logger = structlog.get_logger()

CONSUMER_GROUP = "indexer"
INDEX_STREAM = f"{StreamEventBus.STREAM_PREFIX}index"

# Kinds that never get shed off the backlog: a skipped de-index leaves
# stale/private content searchable, worse than a late one.
_NEVER_SHED_KINDS = {"journal_unindex"}

# Fire-and-forget inline-fallback tasks (stream down/disabled). Mirrors
# journal.py's _RAG_INDEX_TASKS: asyncio holds only a weak ref to a bare
# task, so a module-level strong ref keeps it alive until it finishes; the
# done-callback removes it.
_INLINE_INDEX_TASKS: set[asyncio.Task[None]] = set()


async def drain_inline_index_tasks() -> None:
    """Await all in-flight inline-fallback index tasks (test helper)."""
    await asyncio.gather(*list(_INLINE_INDEX_TASKS), return_exceptions=True)


class _IndexerBusHolder:
    """Holder for the module-level indexer bus singleton (avoids `global`)."""

    instance: StreamEventBus | None = None


def _get_indexer_bus() -> StreamEventBus:
    """Lazily build the indexer's own consumer-group bus.

    A dedicated instance (group ``indexer``) rather than the app-wide
    singleton (group ``roboco-handlers``): this bus only ever carries the
    single INDEX_REQUESTED event type, so ``run_indexer``'s consumer loop
    never competes with the generic handler-fanout bus for the same stream.
    """
    if _IndexerBusHolder.instance is None:
        _IndexerBusHolder.instance = StreamEventBus(group_name=CONSUMER_GROUP)
    return _IndexerBusHolder.instance


OptimalResolver = Callable[[], Coroutine[Any, Any, Any]]


async def enqueue_index_request(
    kind: str,
    payload: dict[str, Any],
    *,
    optimal_resolver: OptimalResolver | None = None,
) -> None:
    """Enqueue an index write for the indexer worker (producer entrypoint).

    Publishes to ``roboco:stream:index``. Falls back to running the index
    inline (a tracked background task, never blocking the caller) when the
    stream is disabled or Redis is unreachable.

    ``optimal_resolver``, when given, is handed to the inline fallback so it
    resolves the OptimalService the same way the calling producer does
    (e.g. journal.py's own lazily-cached ``_get_optimal_service``) instead of
    always re-resolving the global singleton, needed so a caller that holds
    its own service instance (a test injecting a mock, a future per-request
    override) is honored on the inline path too.
    """
    if settings.indexer_stream_enabled:
        try:
            bus = _get_indexer_bus()
            if not bus.is_connected():
                await bus.connect()
            event = Event(
                type=EventType.INDEX_REQUESTED, data={"kind": kind, "payload": payload}
            )
            await bus.publish(event)
            return
        except Exception as e:
            logger.warning(
                "Indexer stream publish failed; running inline", kind=kind, error=str(e)
            )
    _schedule_inline(kind, payload, optimal_resolver=optimal_resolver)


def _schedule_inline(
    kind: str,
    payload: dict[str, Any],
    *,
    optimal_resolver: OptimalResolver | None = None,
) -> None:
    """Run one index request inline, off the caller's await (fire-and-forget)."""

    async def _run() -> None:
        try:
            optimal = await optimal_resolver() if optimal_resolver else None
            await _dispatch_index(kind, payload, optimal=optimal)
        except Exception as e:
            logger.warning(
                "Inline index fallback failed (best-effort)", kind=kind, error=str(e)
            )
            if kind == "journal_entry":
                from roboco.services.rag_index_failures import persist_failure

                await persist_failure("journal_entry", payload, e)

    try:
        task = asyncio.create_task(_run())
    except RuntimeError:
        # No running event loop (sync context): skip best-effort indexing.
        return
    _INLINE_INDEX_TASKS.add(task)
    task.add_done_callback(_INLINE_INDEX_TASKS.discard)


async def _dispatch_index(
    kind: str, payload: dict[str, Any], *, optimal: Any | None = None
) -> None:
    """Run the actual OptimalService call for one enqueued index request.

    ``optimal``, when given, skips resolving the global singleton, the
    inline fallback passes its caller's own resolved instance here (see
    ``enqueue_index_request``'s ``optimal_resolver``).
    """
    if optimal is None:
        from roboco.services.optimal import get_optimal_service

        optimal = await get_optimal_service()
    if kind == "journal_entry":
        await _index_journal_entry(optimal, payload)
    elif kind == "journal_unindex":
        await optimal.unindex_journal_entry(UUID(payload["entry_id"]))
    elif kind == "documentation":
        await optimal.index_documentation(
            payload["sources"],
            project=payload.get("project"),
            provenance=payload.get("provenance", "repo_tree"),
            task_id=payload.get("task_id"),
        )
    elif kind == "learning":
        await _record_learning(optimal, payload)
    else:
        raise ValueError(f"unknown index request kind: {kind}")


async def _index_journal_entry(optimal: Any, payload: dict[str, Any]) -> None:
    from roboco.models.optimal import IndexJournalEntryParams

    await optimal.index_journal_entry(
        IndexJournalEntryParams(
            content=payload["content"],
            entry_type=payload["entry_type"],
            entry_id=UUID(payload["entry_id"]),
            agent_id=UUID(payload["agent_id"]) if payload.get("agent_id") else None,
            task_id=UUID(payload["task_id"]) if payload.get("task_id") else None,
            tags=list(payload.get("tags") or []),
        )
    )


async def _record_learning(optimal: Any, payload: dict[str, Any]) -> None:
    from roboco.services.optimal_brain.indexes.learnings import RecordLearningParams

    await optimal.record_learning(
        RecordLearningParams(
            content=payload["content"],
            category=payload["category"],
            agent_id=UUID(payload["agent_id"]) if payload.get("agent_id") else None,
            agent_role=payload.get("agent_role"),
            task_id=UUID(payload["task_id"]) if payload.get("task_id") else None,
            team=payload.get("team"),
            shareable=payload.get("shareable", True),
            tags=list(payload.get("tags") or []),
        )
    )


async def _handle_index_event(event: Event) -> None:
    """StreamEventBus handler for INDEX_REQUESTED.

    Raises on failure so the bus leaves the message pending for reclaim (and
    eventual dead-letter past ``MAX_DELIVERY_COUNT`` retries); ack happens
    automatically on the bus side when this returns without raising.
    """
    kind = event.data.get("kind", "")
    payload = event.data.get("payload") or {}
    await _dispatch_index(kind, payload)


async def _group_lag(r: Any, group_name: str) -> tuple[int | None, str | None]:
    """Return ``(lag, last_delivered_id)`` for one consumer group on the
    index stream, from ``XINFO GROUPS``.

    ``lag`` is None when the field is absent (Redis <7) or the group isn't
    found; the caller falls back to ``XPENDING``'s still-unacked count.
    """
    try:
        groups = await r.xinfo_groups(INDEX_STREAM)
    except Exception:
        return None, None
    for g in groups or []:
        name = g.get(b"name", g.get("name"))
        if isinstance(name, bytes):
            name = name.decode()
        if name != group_name:
            continue
        lag = g.get(b"lag", g.get("lag"))
        last_id = g.get(b"last-delivered-id", g.get("last-delivered-id"))
        if isinstance(last_id, bytes):
            last_id = last_id.decode()
        return (int(lag) if lag is not None else None), last_id
    return None, None


async def _check_backlog(bus: StreamEventBus) -> None:
    """Shed the oldest sheddable UNDELIVERED requests once the consumer
    group's lag exceeds ``settings.indexer_max_backlog``.

    Lag (Redis 7+) counts only entries never yet delivered to a consumer.
    XLEN counts the whole stream including already-acked history, nothing
    XTRIMs/XDELs a successfully processed entry, so a healthy, fully-drained
    stream would look permanently over-backlog under XLEN and shed forever.
    Falls back to XPENDING's still-unacked ("pending") count on older Redis
    where ``lag`` is absent. Sheds only entries after the group's
    ``last-delivered-id`` so acked history is never a shed candidate.
    """
    r = bus.redis
    if r is None:
        return
    cap = settings.indexer_max_backlog
    lag, last_delivered_id = await _group_lag(r, bus.group_name)
    if lag is None:
        pending = await r.xpending(INDEX_STREAM, bus.group_name)
        lag = pending["pending"] if pending else 0
    if lag <= cap:
        return
    excess = lag - cap
    logger.warning(
        "Indexer backlog over cap; shedding oldest",
        lag=lag,
        cap=cap,
        excess=excess,
    )
    range_min = f"({last_delivered_id}" if last_delivered_id else "-"
    raw = await r.xrange(INDEX_STREAM, min=range_min, max="+", count=excess)
    oldest = cast("list[tuple[bytes, dict[bytes, bytes]]]", raw or [])
    for msg_id, fields in oldest:
        kind = _peek_kind(fields)
        if kind in _NEVER_SHED_KINDS:
            continue
        mid = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
        await bus.dead_letter(INDEX_STREAM, mid, fields, "backlog_shed")
        await r.xdel(INDEX_STREAM, mid)


def _peek_kind(fields: dict[bytes, bytes]) -> str | None:
    """Best-effort ``kind`` read from a raw stream record for the shed check."""
    data = fields.get(b"data")
    if not data:
        return None
    try:
        return Event.from_json(data.decode()).data.get("kind")
    except Exception:
        return None


def _build_indexer_embedder() -> OllamaEmbedder:
    """Build this process's own embedder, tuned lower than the shared default.

    ollama is a single CPU-bound server shared with query embeds (KB search,
    institutional memory); a full-concurrency indexing batch can hold every
    ollama slot and make a concurrent query embed time out. The indexer's own
    embedder runs at ``indexer_embed_concurrency``/``indexer_embed_batch_size``
    (default 1 / 8) instead of the shared embedder's max_concurrent=4 / batch
    32, so a query embed in another process always finds a free slot.
    """
    from roboco.services.optimal_brain.ollama_embedder import OllamaEmbedder

    return OllamaEmbedder(
        model=settings.default_embedding_model,
        base_url=settings.ollama_base_url,
        max_concurrent=settings.indexer_embed_concurrency,
        batch_size=settings.indexer_embed_batch_size,
    )


async def _backlog_loop(bus: StreamEventBus, interval: int = 60) -> None:
    while True:
        try:
            await asyncio.sleep(interval)
            await _check_backlog(bus)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Indexer backlog check failed; will retry", error=str(e))


async def _reconcile_unindexed_playbooks() -> None:
    """Re-index APPROVED playbooks left ``indexed_ok=False`` by a failed
    post-commit embed (e.g. an Ollama restart mid-approval-burst). Best-effort:
    rows stay unindexed and the next boot retries them. Skipped when the
    org-memory loop is off (the index is inert)."""
    if not settings.org_memory_enabled:
        return
    from roboco.db.base import get_session_factory
    from roboco.services.playbook import PlaybookService

    try:
        async with get_session_factory()() as session:
            reconciled = await PlaybookService(session).reconcile_unindexed_approved()
        if reconciled:
            logger.info(
                "Playbook reconcile: re-indexed unindexed approved", count=reconciled
            )
    except Exception as e:
        logger.warning("Playbook reconcile failed; continuing", error=str(e))


async def _reclaim_rag_index_failures(optimal: OptimalService) -> None:
    """Reclaim dead-lettered RAG index writes (embedder 429 after retries, etc.).
    Best-effort: due rows stay in the dead-letter and the next boot retries."""
    from roboco.services.rag_index_failures import reclaim_due

    try:
        reclaimed = await reclaim_due(optimal)
        if reclaimed:
            logger.info(
                "RAG index dead-letter reclaim: re-indexed rows", count=reclaimed
            )
    except Exception as e:
        logger.warning("RAG index dead-letter reclaim failed; continuing", error=str(e))


async def _backfill_unindexed_journals(optimal: OptimalService) -> None:
    """Re-index journal/learning entries silently zero-chunked before the
    per-index chunk-floor fix (see ``backfill_unindexed_journals``). Best-effort:
    the rows stay and the next boot retries them."""
    from roboco.services.rag_index_failures import backfill_unindexed_journals

    try:
        await backfill_unindexed_journals(optimal)
    except Exception as e:
        logger.warning("Journal/learning RAG backfill failed; continuing", error=str(e))


async def reconcile_rag_indexes(optimal: OptimalService | None) -> None:
    """Boot-time RAG reconcile: playbooks, dead-letter reclaim, and the
    journals/learnings zero-chunk backfill. Each pass swallows its own errors.
    No-op when RAG is disabled (``optimal`` is None).

    Owned by :func:`run_indexer` under ``ROBOCO_ROLE=indexer`` (that role never
    runs the FastAPI lifespan) and by the lifespan under ``all``.
    """
    if optimal is None:
        return
    await _reconcile_unindexed_playbooks()
    await _reclaim_rag_index_failures(optimal)
    await _backfill_unindexed_journals(optimal)
    logger.info("RAG index reconcile finished")


def log_reconcile_outcome(task: asyncio.Task[None]) -> None:
    """Surface a background-reconcile crash; each pass already swallows its
    own errors, so anything landing here is an unexpected bug, not a retry."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background RAG reconcile crashed", error=repr(exc))


def schedule_rag_reconcile(optimal: OptimalService | None) -> asyncio.Task[None]:
    """Run the reconcile in the background: a slow backfill must never hold
    up the caller's own startup."""
    task = asyncio.create_task(reconcile_rag_indexes(optimal))
    task.add_done_callback(log_reconcile_outcome)
    return task


async def run_indexer(
    stop: asyncio.Event | None = None, *, dedicated_embedder: bool = False
) -> None:
    """Run the indexer worker: consume ``roboco:stream:index`` and own the
    periodic RAG re-index sweep.

    Consumer group ``indexer``; the bus's own XREADGROUP loop already
    dispatches messages one at a time per consumer (the embedder's own
    ``max_concurrent`` handles batch parallelism, not this loop). Runs until
    ``stop`` is set, or forever when not given (the ``ROBOCO_ROLE=indexer``
    process). In ``ROBOCO_ROLE=all`` this also runs as an in-process task, so
    publish-then-consume in the same process works: ``enqueue_index_request``'s
    fast path and this consumer both hit the same stream. Under ``indexer`` this
    process also owns the boot-time RAG reconcile (the FastAPI lifespan, which
    that role never runs, owns it under ``all``).

    ``dedicated_embedder`` seeds this process's shared-embedder singleton
    with a low-concurrency instance (see :func:`_build_indexer_embedder`),
    correct ONLY when this process runs nothing else that embeds, i.e. the
    standalone ``ROBOCO_ROLE=indexer`` process. Under ``ROBOCO_ROLE=all`` the
    same process also serves interactive query embeds (``roboco_kb_search``,
    ``i_am_idle``, ...) through that same singleton, so seeding it here would
    throttle every one of those too; leave it False there and let indexing
    share the process's normal-concurrency embedder like any other embed
    caller in that process.
    """
    from roboco.services.optimal import get_optimal_service

    if dedicated_embedder:
        from roboco.services.optimal_brain.shared_embedder import (
            set_shared_embedder,
        )

        # Seed this process's embedder BEFORE anything (get_optimal_service's
        # warmup included) can create the default-concurrency one first.
        set_shared_embedder(_build_indexer_embedder())
    # ponytail: ROBOCO_ROLE=all still indexes through the process's normal
    # embedder rather than a private low-concurrency one (that would need an
    # embedder= override threaded through OptimalService + every index
    # plugin's initialize()). Add that if all-in-one deployments measurably
    # see indexing starve a concurrent query embed.

    bus = _get_indexer_bus()
    if not bus.is_connected():
        await bus.connect()
    bus.subscribe(EventType.INDEX_REQUESTED, _handle_index_event)
    await bus.start_listening()

    optimal = await get_optimal_service()
    await optimal.ensure_periodic_update_running()

    # ROBOCO_ROLE=indexer never runs the FastAPI lifespan that schedules the
    # boot-time reconcile for the single-process role, so it owns it here.
    reconcile_task = (
        schedule_rag_reconcile(optimal) if settings.role == "indexer" else None
    )
    backlog_task = asyncio.create_task(_backlog_loop(bus))
    try:
        if stop is not None:
            await stop.wait()
        else:
            await asyncio.Event().wait()
    finally:
        backlog_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await backlog_task
        if reconcile_task is not None:
            reconcile_task.cancel()
        await bus.disconnect()

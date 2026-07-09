"""RAG index dead-letter: durable retry queue for fire-and-forget index writes.

A failed embedder call (e.g. an Ollama 429 after retries) used to be swallowed
+ logged, leaving the entry invisible to ``optimal.search`` /
``similar_memory`` though it lived in the DB. This module persists the failure
to ``rag_index_failures`` instead, counts it for the health route, and
reclaims due rows with backoff — on success the row is deleted, on failure
``attempts`` bumps and ``next_retry_at`` advances. Best-effort throughout: a
persist failure never blocks the caller's commit, and a reclaim failure never
blocks startup.

A second, unrelated problem this module also repairs: before the per-index
chunk-floor fix, ``ingest()`` returned success with ``chunk_count=0`` for
undersized content (journal reflections, distilled learnings) — no exception
was ever raised, so those rows never reached the dead-letter above and never
got a vector row either. ``backfill_unindexed_journals`` re-ingests that
silent-failure history straight from ``journal_entries`` (see its docstring).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, text

from roboco.db.base import get_db_context
from roboco.db.tables import RagIndexFailureTable
from roboco.models.optimal import IndexJournalEntryParams, IndexType
from roboco.services.optimal_brain.indexes.base import IndexConfig

logger = logging.getLogger(__name__)

# Backoff ceiling: 1m, 2m, 4m, ... capped at 1h.
_MAX_BACKOFF_SECONDS = 3600
_BASE_BACKOFF_SECONDS = 60

# Per-pass cap on the startup backfill (below): bounded so a large historical
# backlog can't stall startup. The candidate query already excludes rows below
# the CURRENT floor (they would zero-chunk again), so anything left over past
# the cap is picked up on the next restart — it converges, it may just take
# more than one boot for a very large backlog.
_BACKFILL_CAP = 200


def _backoff(attempts: int) -> timedelta:
    seconds = min(_BASE_BACKOFF_SECONDS * (2 ** (attempts - 1)), _MAX_BACKOFF_SECONDS)
    return timedelta(seconds=seconds)


def _serialize_journal_payload(
    params: IndexJournalEntryParams, *, is_private: bool
) -> dict[str, Any]:
    """JSONB-safe payload for a journal-entry index failure."""
    return {
        "content": params.content,
        "entry_type": params.entry_type,
        "entry_id": str(params.entry_id),
        "agent_id": str(params.agent_id) if params.agent_id else None,
        "task_id": str(params.task_id) if params.task_id else None,
        "tags": list(params.tags) if params.tags else [],
        "is_private": is_private,
    }


async def persist_failure(
    doc_source: str, payload: dict[str, Any], error: Exception
) -> None:
    """Persist a failed fire-and-forget index write to the dead-letter table.

    Opens its own session so the caller's commit is never blocked. Best-effort:
    a persist failure is logged and dropped (the index is already best-effort).
    """
    try:
        async with get_db_context() as db:
            db.add(
                RagIndexFailureTable(
                    doc_source=doc_source,
                    payload=payload,
                    attempts=1,
                    last_error=str(error)[:8000],
                    next_retry_at=datetime.now(UTC) + _backoff(1),
                )
            )
    except Exception:
        # The dead-letter itself is best-effort; never escalate.
        pass


async def count_failures() -> int:
    """Count of dead-lettered index failures — surfaced in the health route."""
    try:
        async with get_db_context() as db:
            result = await db.execute(
                select(func.count()).select_from(RagIndexFailureTable)
            )
            return int(result.scalar_one())
    except Exception:
        return 0


async def reclaim_due(optimal: Any) -> int:
    """Reclaim due dead-letter rows: re-index, delete on success, bump on failure.

    Returns the number of rows successfully reclaimed. Best-effort: a reclaim
    failure never blocks startup. Runs once per call (the lifespan invokes it
    at startup; a future background tick can invoke it periodically).
    """
    reclaimed = 0
    try:
        async with get_db_context() as db:
            now = datetime.now(UTC)
            result = await db.execute(
                select(RagIndexFailureTable)
                .where(RagIndexFailureTable.next_retry_at <= now)
                .order_by(RagIndexFailureTable.next_retry_at)
                .limit(100)
            )
            rows = list(result.scalars().all())
    except Exception:
        return 0

    for row in rows:
        try:
            await _reindex(optimal, row.doc_source, row.payload)
        except Exception as e:
            # Bump attempts + backoff; keep the row.
            try:
                async with get_db_context() as db:
                    row_id = row.id
                    db_row = (
                        await db.execute(
                            select(RagIndexFailureTable).where(
                                RagIndexFailureTable.id == row_id
                            )
                        )
                    ).scalar_one_or_none()
                    if db_row is not None:
                        db_row.attempts = row.attempts + 1
                        db_row.last_error = str(e)[:8000]
                        db_row.next_retry_at = datetime.now(UTC) + _backoff(
                            row.attempts + 1
                        )
            except Exception:
                pass
            continue
        # Success: delete the row.
        try:
            async with get_db_context() as db:
                await db.execute(
                    delete(RagIndexFailureTable).where(
                        RagIndexFailureTable.id == row.id
                    )
                )
            reclaimed += 1
        except Exception:
            pass
    return reclaimed


async def _reindex(optimal: Any, doc_source: str, payload: dict[str, Any]) -> None:
    """Replay the original index call from the serialized payload."""
    if doc_source == "journal_entry":
        await _reindex_journal_entry(optimal, payload)
    elif doc_source == "completion_learning":
        await _reindex_completion_learning(optimal, payload)
    else:
        raise ValueError(f"unknown rag index doc_source: {doc_source}")


async def _reindex_journal_entry(optimal: Any, payload: dict[str, Any]) -> None:
    """Replay a journal-entry index (and its learning recording if applicable)."""
    # Mirrors the original path (journal._schedule_rag_index): private
    # reflections stay out of the shared JOURNALS corpus; a private
    # learning is still recorded, just non-shareable.
    is_private = bool(payload.get("is_private"))
    if not is_private:
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
    if payload["entry_type"] == "learning":
        from roboco.services.optimal_brain.indexes.learnings import (
            RecordLearningParams as _JournalLearningParams,
        )

        await optimal.record_learning(
            _JournalLearningParams(
                content=payload["content"],
                category="journal_learning",
                agent_id=UUID(payload["agent_id"]) if payload.get("agent_id") else None,
                task_id=UUID(payload["task_id"]) if payload.get("task_id") else None,
                shareable=not is_private,
                tags=list(payload.get("tags") or []),
            )
        )


async def _reindex_completion_learning(optimal: Any, payload: dict[str, Any]) -> None:
    """Replay a completion-learning index (delegates to the LEARNINGS plugin)."""
    from roboco.services.optimal_brain.indexes.learnings import (
        RecordLearningParams as _CompletionLearningParams,
    )

    await optimal.record_learning(
        _CompletionLearningParams(
            content=payload["content"],
            category=payload["learning_type"],
            agent_id=UUID(payload["agent_id"]),
            agent_role=payload["agent_role"],
            task_id=UUID(payload["task_id"]) if payload.get("task_id") else None,
            team=None,
            shareable=payload.get("scope") != "personal",
            tags=list(payload.get("tags") or []),
        )
    )


def _learning_source(content: str) -> str:
    """Derive a learning's doc_source exactly as record_learning does.

    Mirrors ``LearningsIndexPlugin.record_learning`` (learnings.py): the
    doc_id is a hash of the raw content, not the entry id, so presence in
    ``chunks_learnings`` can't be joined by primary key — this lets the
    backfill below compute the same source and check for it directly.
    """
    content_hash = hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()[:16]
    return f"roboco://learnings/lrn-{content_hash}"


async def _backfill_journals(optimal: Any) -> tuple[int, int]:
    """Re-ingest non-private journal entries with no ``chunks_journals`` row.

    Candidates are entries at/above the JOURNALS floor (a still-undersized
    entry would zero-chunk again — excluded here so it's never retried
    forever) whose ``roboco://journals/<id>`` source has zero chunk rows.
    Returns ``(processed, still_missing)`` for this capped batch.
    """
    if not optimal.is_index_registered(IndexType.JOURNALS):
        return 0, 0
    floor = IndexConfig.from_settings(IndexType.JOURNALS).min_chunk_length
    async with get_db_context() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT je.id, je.content, je.type, j.agent_id, je.task_id, je.tags
                    FROM journal_entries je
                    JOIN journals j ON j.id = je.journal_id
                    LEFT JOIN chunks_journals cj
                        ON cj.source = 'roboco://journals/' || je.id::text
                    WHERE cj.source IS NULL
                        AND je.is_private = false
                        AND length(je.content) >= :floor
                    ORDER BY je.created_at
                    LIMIT :cap
                    """
                ),
                {"floor": floor, "cap": _BACKFILL_CAP},
            )
        ).all()

    processed = 0
    for row in rows:
        try:
            await _reindex_journal_entry(
                optimal,
                {
                    "content": row.content,
                    "entry_type": row.type,
                    "entry_id": str(row.id),
                    "agent_id": str(row.agent_id) if row.agent_id else None,
                    "task_id": str(row.task_id) if row.task_id else None,
                    "tags": list(row.tags or []),
                    "is_private": False,
                },
            )
            processed += 1
        except Exception as e:
            logger.warning(
                "RAG backfill: journal re-index failed (best-effort), "
                f"entry_id={row.id}, error={e}"
            )
    return processed, len(rows) - processed


async def _backfill_learnings(optimal: Any) -> tuple[int, int]:
    """Re-ingest LEARNING journal entries with no matching ``chunks_learnings`` row.

    A learning's doc_source is a content hash, not the entry id, so presence
    can't be joined in SQL — hash each candidate (``_learning_source``) and
    batch-check ``chunks_learnings`` for those exact sources. Independent of
    :func:`_backfill_journals`: a LEARNING entry can pass the (lower) JOURNALS
    floor but fail the (higher) LEARNINGS floor, so it may already have a
    ``chunks_journals`` row while still missing here.
    """
    if not optimal.is_index_registered(IndexType.LEARNINGS):
        return 0, 0
    floor = IndexConfig.from_settings(IndexType.LEARNINGS).min_chunk_length
    async with get_db_context() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT je.id, je.content, j.agent_id, je.task_id, je.tags,
                        je.is_private
                    FROM journal_entries je
                    JOIN journals j ON j.id = je.journal_id
                    WHERE je.type = 'learning' AND length(je.content) >= :floor
                    ORDER BY je.created_at
                    LIMIT :cap
                    """
                ),
                {"floor": floor, "cap": _BACKFILL_CAP},
            )
        ).all()
        if not rows:
            return 0, 0

        sources = [_learning_source(row.content) for row in rows]
        existing = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT source FROM chunks_learnings "
                        "WHERE source = ANY(CAST(:sources AS text[]))"
                    ),
                    {"sources": sources},
                )
            ).all()
        }

    from roboco.services.optimal_brain.indexes.learnings import (
        RecordLearningParams as _JournalLearningParams,
    )

    processed = 0
    missing = 0
    for row, source in zip(rows, sources, strict=True):
        if source in existing:
            continue
        missing += 1
        try:
            await optimal.record_learning(
                _JournalLearningParams(
                    content=row.content,
                    category="journal_learning",
                    agent_id=row.agent_id,
                    task_id=row.task_id,
                    shareable=not row.is_private,
                    tags=list(row.tags or []),
                )
            )
            processed += 1
        except Exception as e:
            logger.warning(
                "RAG backfill: learning re-index failed (best-effort), "
                f"entry_id={row.id}, error={e}"
            )
    return processed, missing - processed


async def backfill_unindexed_journals(optimal: Any) -> dict[str, int]:
    """Re-ingest journal/learning entries silently zero-chunked before the
    per-index chunk-floor fix.

    ``ingest()`` used to return success with ``chunk_count=0`` for undersized
    content, so historical journal entries and their derived learnings were
    durably recorded in ``journal_entries`` but never landed a row in
    ``chunks_journals`` / ``chunks_learnings`` — invisible to RAG search
    though the dead-letter above only ever covered rows that raised, not rows
    that silently zero-chunked.

    Each pass is independently gated (skipped if that index's plugin never
    initialized), capped per boot, and best-effort per row — one failing row
    never blocks the rest. Best-effort at this level too: a hard failure
    (e.g. a lost DB connection) never blocks startup, and it converges over
    restarts since a successfully re-indexed row simply stops matching the
    candidate query.
    """
    try:
        j_processed, j_remaining = await _backfill_journals(optimal)
    except Exception as e:
        logger.warning(f"RAG backfill: journals pass failed; continuing, error={e}")
        j_processed, j_remaining = 0, 0
    try:
        l_processed, l_remaining = await _backfill_learnings(optimal)
    except Exception as e:
        logger.warning(f"RAG backfill: learnings pass failed; continuing, error={e}")
        l_processed, l_remaining = 0, 0

    if j_processed or l_processed:
        logger.info(
            "RAG backfill: re-indexed historical zero-chunk entries "
            f"(journals processed={j_processed} remaining={j_remaining}, "
            f"learnings processed={l_processed} remaining={l_remaining})"
        )
    return {
        "journals_processed": j_processed,
        "journals_remaining": j_remaining,
        "learnings_processed": l_processed,
        "learnings_remaining": l_remaining,
    }


__all__ = [
    "_serialize_journal_payload",
    "backfill_unindexed_journals",
    "count_failures",
    "persist_failure",
    "reclaim_due",
]

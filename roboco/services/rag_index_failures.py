"""RAG index dead-letter: durable retry queue for fire-and-forget index writes.

A failed embedder call (e.g. an Ollama 429 after retries) used to be swallowed
+ logged, leaving the entry invisible to ``optimal.search`` /
``similar_memory`` though it lived in the DB. This module persists the failure
to ``rag_index_failures`` instead, counts it for the health route, and
reclaims due rows with backoff — on success the row is deleted, on failure
``attempts`` bumps and ``next_retry_at`` advances. Best-effort throughout: a
persist failure never blocks the caller's commit, and a reclaim failure never
blocks startup.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select

from roboco.db.base import get_db_context
from roboco.db.tables import RagIndexFailureTable
from roboco.models.optimal import IndexJournalEntryParams

# Backoff ceiling: 1m, 2m, 4m, ... capped at 1h.
_MAX_BACKOFF_SECONDS = 3600
_BASE_BACKOFF_SECONDS = 60


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
    # A learning entry also records into the LEARNINGS index unless private.
    if payload["entry_type"] == "learning" and not payload.get("is_private"):
        from roboco.services.optimal_brain.indexes.learnings import (
            RecordLearningParams as _JournalLearningParams,
        )

        await optimal.record_learning(
            _JournalLearningParams(
                content=payload["content"],
                category="journal_learning",
                agent_id=UUID(payload["agent_id"]) if payload.get("agent_id") else None,
                task_id=UUID(payload["task_id"]) if payload.get("task_id") else None,
                shareable=True,
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


__all__ = [
    "_serialize_journal_payload",
    "count_failures",
    "persist_failure",
    "reclaim_due",
]

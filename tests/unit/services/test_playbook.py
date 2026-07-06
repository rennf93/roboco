"""M23 — durable playbook index-state tracking + startup reconcile.

An Ollama restart mid-approval-burst left an APPROVED playbook's
``index_approved`` step swallowed (logged + dropped), so the row was
APPROVED in the DB but absent from the PLAYBOOKS RAG corpus — the Auditor
saw "approved" while agents never surfaced the procedure. The fix is a
durable ``indexed_ok`` flag set ONLY on a successful embed, plus a startup
reconcile that re-indexes APPROVED-but-``indexed_ok=False`` rows.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.models.base import PlaybookStatus
from roboco.services.optimal_brain.indexes.base import IngestResult
from roboco.services.playbook import PlaybookService


def _approved_playbook(*, indexed_ok: bool = False) -> Any:
    pb = MagicMock()
    pb.id = uuid4()
    pb.status = PlaybookStatus.APPROVED.value
    pb.title = "Roll back a bad migration"
    pb.problem = "A migration shipped data loss."
    pb.procedure = "1. pg_restore the snapshot.\n2. Re-apply forward migrations."
    pb.tags = ["backend"]
    pb.team = "backend"
    pb.scope = "org"
    pb.indexed_ok = indexed_ok
    pb.indexed_at = None
    return pb


def _session_returning(playbooks: list[Any]) -> Any:
    session = MagicMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = playbooks
    result.scalars.return_value = scalars
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_reconcile_reindexes_unindexed_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconcile finds APPROVED-but-indexed_ok=False rows and re-indexes them,
    stamping ``indexed_ok=True`` + ``indexed_at`` only on a successful embed."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    pb = _approved_playbook(indexed_ok=False)
    session = _session_returning([pb])
    svc = PlaybookService(session)

    optimal = MagicMock()
    optimal.index_playbook = AsyncMock(
        return_value=IngestResult(doc_id=str(pb.id), chunk_count=3, success=True)
    )
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        count = await svc.reconcile_unindexed_approved()

    assert count == 1
    optimal.index_playbook.assert_awaited_once()
    assert pb.indexed_ok is True
    assert pb.indexed_at is not None
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_skips_when_org_memory_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """org_memory_enabled=False ⇒ the index is inert; reconcile no-ops."""
    monkeypatch.setattr(settings, "org_memory_enabled", False)
    session = _session_returning([_approved_playbook()])
    svc = PlaybookService(session)

    count = await svc.reconcile_unindexed_approved()

    assert count == 0
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_rolls_back_on_index_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-playbook failure is rolled back; the loop continues (best-effort)."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    pb = _approved_playbook(indexed_ok=False)
    session = _session_returning([pb])
    svc = PlaybookService(session)

    optimal = MagicMock()
    optimal.index_playbook = AsyncMock(side_effect=RuntimeError("ollama still down"))
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        count = await svc.reconcile_unindexed_approved()

    assert count == 0
    assert pb.indexed_ok is False
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_index_approved_failure_does_not_stamp_indexed_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``index_approved`` sets ``indexed_ok=True`` ONLY on a successful embed —
    a failed embed (the embedder returned success=False) leaves the row
    APPROVED-but-unindexed for the reconcile to retry."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    pb = _approved_playbook(indexed_ok=False)
    session = _session_returning([])
    svc = PlaybookService(session)

    optimal = MagicMock()
    optimal.index_playbook = AsyncMock(
        return_value=IngestResult(
            doc_id=str(pb.id), chunk_count=0, success=False, error="ollama down"
        )
    )
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        await svc.index_approved(pb)

    optimal.index_playbook.assert_awaited_once()
    assert pb.indexed_ok is False
    assert pb.indexed_at is None


@pytest.mark.asyncio
async def test_index_approved_exception_does_not_stamp_indexed_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception out of ``index_playbook`` is swallowed (best-effort) and
    ``indexed_ok`` stays False — the row remains a reconcile candidate."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    pb = _approved_playbook(indexed_ok=False)
    session = _session_returning([])
    svc = PlaybookService(session)

    optimal = MagicMock()
    optimal.index_playbook = AsyncMock(side_effect=RuntimeError("embedder crashed"))
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        await svc.index_approved(pb)  # must not raise

    assert pb.indexed_ok is False
    assert pb.indexed_at is None


@pytest.mark.asyncio
async def test_index_approved_success_stamps_indexed_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful embed stamps ``indexed_ok=True`` + ``indexed_at`` and
    flushes so the caller's commit persists the flag."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    pb = _approved_playbook(indexed_ok=False)
    session = _session_returning([])
    svc = PlaybookService(session)

    optimal = MagicMock()
    optimal.index_playbook = AsyncMock(
        return_value=IngestResult(doc_id=str(pb.id), chunk_count=3, success=True)
    )
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        await svc.index_approved(pb)

    assert pb.indexed_ok is True
    assert pb.indexed_at is not None
    session.flush.assert_awaited_once()

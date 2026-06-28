"""F011 — PlaybookService.reject must de-index the playbook.

A rejected/archived playbook that was previously approved stays in the
PLAYBOOKS RAG index (no de-index on reject/archive), so it keeps surfacing
in agent briefings as a stale, no-longer-canonical procedure. The fix
mirrors the index-on-approve path: ``reject`` calls an
``_unindex_playbook`` helper (gated on ``org_memory_enabled``, best-effort)
that removes the playbook's chunks + tracking row.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.models.base import PlaybookStatus
from roboco.services.playbook import PlaybookService


def _mock_playbook(playbook_id, *, status=PlaybookStatus.APPROVED.value):
    pb = MagicMock()
    pb.id = playbook_id
    pb.status = status
    pb.title = "Retry a flaky pg test"
    pb.problem = "..."
    pb.procedure = "..."
    pb.tags = ["backend"]
    pb.team = "backend"
    pb.scope = "org"
    return pb


@pytest.mark.asyncio
async def test_reject_deindexes_approved_playbook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejecting an approved (indexed) playbook removes it from the index."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    playbook_id = uuid4()
    pb = _mock_playbook(playbook_id, status=PlaybookStatus.APPROVED.value)

    session = MagicMock()
    # _get_or_raise returns the playbook; flush is a no-op.
    result = MagicMock()
    result.scalar_one_or_none.return_value = pb
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()

    optimal = MagicMock()
    optimal.unindex_playbook = AsyncMock(return_value=None)

    svc = PlaybookService(session)
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        out = await svc.reject(playbook_id, approver_id=uuid4(), reason="stale")

    assert out.status == PlaybookStatus.ARCHIVED.value
    optimal.unindex_playbook.assert_awaited_once_with(str(playbook_id))


@pytest.mark.asyncio
async def test_reject_of_unindexed_draft_does_not_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A draft (never indexed) reject still de-indexes (idempotent no-op) and
    never errors the curation."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    playbook_id = uuid4()
    pb = _mock_playbook(playbook_id, status=PlaybookStatus.DRAFT.value)

    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = pb
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()

    optimal = MagicMock()
    optimal.unindex_playbook = AsyncMock(return_value=None)

    svc = PlaybookService(session)
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        out = await svc.reject(playbook_id, approver_id=uuid4(), reason="nope")

    assert out.status == PlaybookStatus.ARCHIVED.value
    # De-index is still called (idempotent — the store no-ops on an absent
    # source), so a draft reject mirrors the approved reject path uniformly.
    optimal.unindex_playbook.assert_awaited_once_with(str(playbook_id))


@pytest.mark.asyncio
async def test_reject_skips_deindex_when_org_memory_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """org_memory_enabled=False ⇒ the index is inert; reject must not touch it."""
    monkeypatch.setattr(settings, "org_memory_enabled", False)
    playbook_id = uuid4()
    pb = _mock_playbook(playbook_id)

    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = pb
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()

    optimal = MagicMock()
    optimal.unindex_playbook = AsyncMock(return_value=None)

    svc = PlaybookService(session)
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        await svc.reject(playbook_id, approver_id=uuid4(), reason="nope")

    optimal.unindex_playbook.assert_not_awaited()

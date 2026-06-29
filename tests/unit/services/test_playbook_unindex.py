"""PlaybookService.reject/archive must flush the status change WITHOUT
de-indexing inline, and ``unindex_playbook`` is the separate post-commit step.

A rejected/archived playbook that was previously approved must stop surfacing in
agent briefings, so the curation drop its chunks + tracking row. Originally
(F011) ``reject`` called an ``_unindex_playbook`` helper inline. F057 split that
out: the RAG index write runs through its own auto-committing pool connection,
so de-indexing inline (before the caller commits the status) would drop a
playbook from the corpus even if the status transaction rolled back — a
divergence. So ``reject``/``archive`` now flush the status ONLY;
``unindex_playbook`` is a separate public step the caller runs AFTER
committing. F109 split the APPROVED->archived retire path into ``archive``
(distinct from ``reject``, which declines a DRAFT). Both helpers stay gated on
``org_memory_enabled`` (inert when the loop is off) and best-effort.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.models.base import PlaybookStatus
from roboco.services.playbook import PlaybookService


def _mock_playbook(
    playbook_id: Any, *, status: str = PlaybookStatus.APPROVED.value
) -> MagicMock:
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


def _session_with(pb: Any) -> MagicMock:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = pb
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_archive_archives_but_does_not_deindex_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``archive`` flushes the ARCHIVED status but must NOT touch the RAG index —
    the de-index is a separate post-commit step (F057 ordering). Archive retires
    an APPROVED playbook (F109); the previously-approved one was indexed on
    approval, so the post-commit ``unindex_playbook`` is what removes it."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    playbook_id = uuid4()
    pb = _mock_playbook(playbook_id, status=PlaybookStatus.APPROVED.value)
    svc = PlaybookService(_session_with(pb))

    optimal = MagicMock()
    optimal.unindex_playbook = AsyncMock(return_value=None)
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        out = await svc.archive(playbook_id, approver_id=uuid4())

    assert out.status == PlaybookStatus.ARCHIVED.value
    optimal.unindex_playbook.assert_not_awaited()


@pytest.mark.asyncio
async def test_unindex_playbook_deindexes_approved_playbook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``unindex_playbook`` (the post-commit step) removes an approved playbook
    from the index."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    playbook_id = uuid4()
    pb = _mock_playbook(playbook_id, status=PlaybookStatus.APPROVED.value)
    svc = PlaybookService(_session_with(pb))

    optimal = MagicMock()
    optimal.unindex_playbook = AsyncMock(return_value=None)
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        await svc.unindex_playbook(pb)

    optimal.unindex_playbook.assert_awaited_once_with(str(playbook_id))


@pytest.mark.asyncio
async def test_unindex_playbook_skips_when_org_memory_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """org_memory_enabled=False ⇒ the index is inert; ``unindex_playbook`` must
    not touch it."""
    monkeypatch.setattr(settings, "org_memory_enabled", False)
    playbook_id = uuid4()
    pb = _mock_playbook(playbook_id)
    svc = PlaybookService(_session_with(pb))

    optimal = MagicMock()
    optimal.unindex_playbook = AsyncMock(return_value=None)
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        await svc.unindex_playbook(pb)

    optimal.unindex_playbook.assert_not_awaited()

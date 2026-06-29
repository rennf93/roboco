"""Unit tests for WorkSessionService gateway-backfill methods."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.work_session import WorkSessionStatus
from roboco.services.work_session import WorkSessionService


def _service() -> WorkSessionService:
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    return WorkSessionService(session)


def _bind(svc: WorkSessionService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


@pytest.mark.asyncio
async def test_files_changed_returns_files_modified_list() -> None:
    svc = _service()
    fake_session = MagicMock(files_modified=["roboco/api/app.py", "README.md"])
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    out = await svc.files_changed(uuid4())
    assert out == ["roboco/api/app.py", "README.md"]


@pytest.mark.asyncio
async def test_files_changed_returns_empty_list_when_session_missing() -> None:
    svc = _service()
    _bind(svc, "get", AsyncMock(return_value=None))
    assert await svc.files_changed(uuid4()) == []


@pytest.mark.asyncio
async def test_files_changed_handles_none_files_modified() -> None:
    svc = _service()
    fake_session = MagicMock(files_modified=None)
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    assert await svc.files_changed(uuid4()) == []


@pytest.mark.asyncio
async def test_has_unpushed_commits_false_when_no_commits() -> None:
    svc = _service()
    fake_session = MagicMock(commits=[], pr_number=None)
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    assert await svc.has_unpushed_commits(uuid4()) is False


@pytest.mark.asyncio
async def test_has_unpushed_commits_true_when_commits_but_no_pr() -> None:
    svc = _service()
    fake_session = MagicMock(commits=["abc", "def"], pr_number=None)
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    assert await svc.has_unpushed_commits(uuid4()) is True


@pytest.mark.asyncio
async def test_has_unpushed_commits_false_when_pr_exists() -> None:
    svc = _service()
    fake_session = MagicMock(commits=["abc"], pr_number=42)
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    assert await svc.has_unpushed_commits(uuid4()) is False


@pytest.mark.asyncio
async def test_has_unpushed_commits_false_when_session_missing() -> None:
    svc = _service()
    _bind(svc, "get", AsyncMock(return_value=None))
    assert await svc.has_unpushed_commits(uuid4()) is False


# ---------------------------------------------------------------------------
# merge_pr must be idempotent + active-guarded like close()/complete()
# ---------------------------------------------------------------------------


def _merge_service() -> tuple[WorkSessionService, MagicMock]:
    """A service whose session flush is observable. Returns ``(svc, session)`` so
    the tests can assert that a guarded no-op never reached ``flush``."""
    session = MagicMock()
    session.flush = AsyncMock()
    svc = WorkSessionService(session)
    return svc, session


@pytest.mark.asyncio
async def test_merge_pr_completes_active_session() -> None:
    """Happy path: an ACTIVE session is recorded as merged + COMPLETED."""

    original_merger = uuid4()
    ws = MagicMock(
        status=WorkSessionStatus.ACTIVE, pr_number=42, branch_name="feature/x"
    )
    svc, session = _merge_service()
    _bind(svc, "get", AsyncMock(return_value=ws))

    result = await svc.merge_pr(uuid4(), original_merger)

    assert result is ws
    assert ws.pr_status == "merged"
    assert ws.merged_by == original_merger
    assert ws.pr_merged_at is not None
    assert ws.status == WorkSessionStatus.COMPLETED
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_merge_pr_idempotent_on_already_completed_preserves_audit_trail() -> None:
    """A retried merge after a successful-but-unconfirmed GitHub merge must NOT
    overwrite the original ``merged_by`` / ``pr_merged_at`` — the merge audit
    trail is preserved. Mirrors close()'s idempotency guard."""

    original_merger = uuid4()
    original_ts = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    retry_merger = uuid4()
    ws = MagicMock(
        status=WorkSessionStatus.COMPLETED,
        pr_number=42,
        merged_by=original_merger,
        pr_merged_at=original_ts,
        pr_status="merged",
    )
    svc, session = _merge_service()
    _bind(svc, "get", AsyncMock(return_value=ws))

    result = await svc.merge_pr(uuid4(), retry_merger)

    assert result is ws
    # Audit trail untouched — retry did not overwrite the original merger/ts.
    assert ws.merged_by == original_merger
    assert ws.pr_merged_at == original_ts
    assert ws.status == WorkSessionStatus.COMPLETED
    # No mutation happened, so flush must not have run.
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_pr_does_not_resurrect_abandoned_session() -> None:
    """``merge_pr`` on an ABANDONED session must NOT flip it to COMPLETED — that
    would silently undo the single-active invariant's abandonment and make
    discarded work look like a successful merge."""

    ws = MagicMock(status=WorkSessionStatus.ABANDONED, pr_number=42, merged_by=None)
    svc, session = _merge_service()
    _bind(svc, "get", AsyncMock(return_value=ws))

    result = await svc.merge_pr(uuid4(), uuid4())

    assert result is ws
    # Not resurrected — stays abandoned, no merger recorded.
    assert ws.status == WorkSessionStatus.ABANDONED
    assert ws.merged_by is None
    session.flush.assert_not_awaited()

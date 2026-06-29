"""PlaybookService — draft + Auditor curation transitions (real Postgres)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.config import settings as cfg
from roboco.models.base import PlaybookStatus
from roboco.models.playbook import PlaybookCreate
from roboco.services.base import ConflictError, NotFoundError
from roboco.services.playbook import PlaybookService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _create(title: str = "Retry a flaky pg test", **kw: Any) -> PlaybookCreate:
    base: dict[str, Any] = {
        "title": title,
        "problem": "A pg integration test fails intermittently on connection reset.",
        "procedure": "1. Wrap the fixture in a retry.\n2. Assert idempotency.",
        "tags": ["backend"],
        "scope": "org",
    }
    base.update(kw)
    return PlaybookCreate(**base)


@pytest.mark.asyncio
async def test_draft_creates_a_draft_with_derived_slug(
    db_session: AsyncSession,
) -> None:
    svc = PlaybookService(db_session)
    pb = await svc.draft(_create(title="Retry Flaky PG!"), created_by=uuid4())
    assert pb.status == PlaybookStatus.DRAFT
    assert pb.slug == "retry-flaky-pg"


@pytest.mark.asyncio
async def test_draft_then_approve_flips_status_and_stamps(
    db_session: AsyncSession,
) -> None:
    svc = PlaybookService(db_session)
    auditor = uuid4()
    pb = await svc.draft(_create(), created_by=uuid4())
    approved = await svc.approve(pb.id, approver_id=auditor)
    assert approved.status == PlaybookStatus.APPROVED
    assert approved.approved_by == auditor
    assert approved.approved_at is not None


@pytest.mark.asyncio
async def test_reject_archives(db_session: AsyncSession) -> None:
    svc = PlaybookService(db_session)
    pb = await svc.draft(_create(), created_by=uuid4())
    out = await svc.reject(
        pb.id, approver_id=uuid4(), reason="duplicate of an existing one"
    )
    assert out.status == PlaybookStatus.ARCHIVED


@pytest.mark.asyncio
async def test_list_drafts_and_approved_partition(db_session: AsyncSession) -> None:
    svc = PlaybookService(db_session)
    d = await svc.draft(_create(title="Draft one"), created_by=uuid4())
    a = await svc.draft(_create(title="Approved one"), created_by=uuid4())
    await svc.approve(a.id, approver_id=uuid4())

    draft_ids = {p.id for p in await svc.list_drafts()}
    approved_ids = {p.id for p in await svc.list_approved()}
    assert d.id in draft_ids and d.id not in approved_ids
    assert a.id in approved_ids and a.id not in draft_ids


@pytest.mark.asyncio
async def test_duplicate_slug_raises_conflict(db_session: AsyncSession) -> None:
    svc = PlaybookService(db_session)
    await svc.draft(_create(title="Same Title"), created_by=uuid4())
    with pytest.raises(ConflictError):
        await svc.draft(_create(title="Same Title"), created_by=uuid4())


@pytest.mark.asyncio
async def test_approve_missing_raises_notfound(db_session: AsyncSession) -> None:
    svc = PlaybookService(db_session)
    with pytest.raises(NotFoundError):
        await svc.approve(uuid4(), approver_id=uuid4())


@pytest.mark.asyncio
async def test_source_task_id_is_recorded(db_session: AsyncSession) -> None:
    svc = PlaybookService(db_session)
    task_id = uuid4()
    pb = await svc.draft(_create(source_task_id=task_id), created_by=uuid4())
    assert str(task_id) in pb.source_task_ids


@pytest.mark.asyncio
async def test_approve_indexes_when_org_memory_on(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "org_memory_enabled", True)
    fake_optimal = AsyncMock()
    fake_optimal.index_playbook = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    )
    svc = PlaybookService(db_session)
    pb = await svc.draft(_create(title="Index me"), created_by=uuid4())
    approved = await svc.approve(pb.id, approver_id=uuid4())
    # approve() only flushes the status; indexing is the separate post-commit
    # step the route/verb runs after committing (so the index never leads the
    # status transaction). Mirror that ordering here.
    await svc.index_approved(approved)
    fake_optimal.index_playbook.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_does_not_index_when_off(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "org_memory_enabled", False)
    getter = AsyncMock()
    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", getter)
    svc = PlaybookService(db_session)
    pb = await svc.draft(_create(title="Do not index"), created_by=uuid4())
    await svc.approve(pb.id, approver_id=uuid4())
    getter.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_survives_index_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "org_memory_enabled", True)
    fake_optimal = AsyncMock()
    fake_optimal.index_playbook = AsyncMock(side_effect=RuntimeError("ollama down"))
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=fake_optimal),
    )
    svc = PlaybookService(db_session)
    pb = await svc.draft(_create(title="Resilient"), created_by=uuid4())
    approved = await svc.approve(pb.id, approver_id=uuid4())  # must not raise
    assert approved.status == PlaybookStatus.APPROVED


# --- F109: approve/reject/archive status-precondition guards ---------------- #
# The lifecycle is draft -> approved | archived, both terminal. approve/reject
# only act on a DRAFT; archive only retires an APPROVED playbook. An archived
# playbook is terminal — none of the three may touch it again. Without these
# guards an archived playbook could be re-approved and an approved one rejected,
# silently undoing a finished curation.


@pytest.mark.asyncio
async def test_approve_rejects_already_approved(db_session: AsyncSession) -> None:
    svc = PlaybookService(db_session)
    pb = await svc.draft(_create(title="Once"), created_by=uuid4())
    await svc.approve(pb.id, approver_id=uuid4())
    with pytest.raises(ConflictError):
        await svc.approve(pb.id, approver_id=uuid4())


@pytest.mark.asyncio
async def test_approve_rejects_archived(db_session: AsyncSession) -> None:
    svc = PlaybookService(db_session)
    pb = await svc.draft(_create(title="Done"), created_by=uuid4())
    await svc.reject(pb.id, approver_id=uuid4(), reason="duplicate")
    with pytest.raises(ConflictError):
        await svc.approve(pb.id, approver_id=uuid4())


@pytest.mark.asyncio
async def test_reject_rejects_already_approved(db_session: AsyncSession) -> None:
    svc = PlaybookService(db_session)
    pb = await svc.draft(_create(title="Published svc"), created_by=uuid4())
    await svc.approve(pb.id, approver_id=uuid4())
    with pytest.raises(ConflictError):
        await svc.reject(pb.id, approver_id=uuid4(), reason="changed my mind")


@pytest.mark.asyncio
async def test_reject_rejects_archived(db_session: AsyncSession) -> None:
    svc = PlaybookService(db_session)
    pb = await svc.draft(_create(title="Closed"), created_by=uuid4())
    await svc.reject(pb.id, approver_id=uuid4(), reason="duplicate")
    with pytest.raises(ConflictError):
        await svc.reject(pb.id, approver_id=uuid4(), reason="again")


@pytest.mark.asyncio
async def test_archive_retires_approved(db_session: AsyncSession) -> None:
    svc = PlaybookService(db_session)
    auditor = uuid4()
    pb = await svc.draft(_create(title="Retire svc"), created_by=uuid4())
    await svc.approve(pb.id, approver_id=auditor)
    archived = await svc.archive(pb.id, approver_id=auditor)
    assert archived.status == PlaybookStatus.ARCHIVED
    assert archived.approved_by == auditor
    assert archived.approved_at is not None


@pytest.mark.asyncio
async def test_archive_rejects_draft(db_session: AsyncSession) -> None:
    svc = PlaybookService(db_session)
    pb = await svc.draft(_create(title="Not yet"), created_by=uuid4())
    with pytest.raises(ConflictError):
        await svc.archive(pb.id, approver_id=uuid4())


@pytest.mark.asyncio
async def test_archive_rejects_already_archived(db_session: AsyncSession) -> None:
    svc = PlaybookService(db_session)
    pb = await svc.draft(_create(title="Twice"), created_by=uuid4())
    await svc.approve(pb.id, approver_id=uuid4())
    await svc.archive(pb.id, approver_id=uuid4())
    with pytest.raises(ConflictError):
        await svc.archive(pb.id, approver_id=uuid4())

"""PlaybookService — draft + Auditor curation transitions (real Postgres)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.config import settings as cfg
from roboco.db.tables import BoardProgramCycleTable
from roboco.foundation import identity as _foundation
from roboco.models.base import PlaybookStatus
from roboco.models.playbook import PlaybookCreate
from roboco.services.base import ConflictError, NotFoundError
from roboco.services.playbook import PlaybookService
from sqlalchemy import select

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
    approved = await svc.approve(pb.id, approver_id=uuid4())
    # Simulate a successful post-commit index so the row is durably indexed;
    # a second approve on an already-indexed approved playbook is the
    # conflict (M23: an APPROVED-but-unindexed row is re-approvable as a
    # retry path, not a conflict).
    approved.indexed_ok = True
    await db_session.flush()
    with pytest.raises(ConflictError):
        await svc.approve(pb.id, approver_id=uuid4())


@pytest.mark.asyncio
async def test_approve_retry_unindexed_approved(
    db_session: AsyncSession,
) -> None:
    """M23: an APPROVED playbook whose ``indexed_ok`` is still False (the
    post-commit index write failed or never ran) is re-approvable — the
    retry path the Auditor/CEO uses to re-run ``index_approved``. The status
    and approval provenance are NOT re-stamped on retry."""
    svc = PlaybookService(db_session)
    approver = uuid4()
    pb = await svc.draft(_create(title="Retry me"), created_by=uuid4())
    first = await svc.approve(pb.id, approver_id=approver)
    assert first.indexed_ok is False
    first_stamped = first.approved_at
    # A mid-approval Ollama outage leaves indexed_ok=False; re-approve is allowed.
    second = await svc.approve(pb.id, approver_id=approver)
    assert second.status == PlaybookStatus.APPROVED
    # No re-stamp on retry — the approval provenance is preserved.
    assert second.approved_at == first_stamped
    assert second.indexed_ok is False


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
async def test_archive_preserves_approver_and_stamps_archiver(
    db_session: AsyncSession,
) -> None:
    """#76: archive is a DISTINCT curation act from approve — retiring an
    approved playbook must NOT overwrite who approved it. The archiver is
    recorded in ``archived_by``/``archived_at``; ``approved_by``/``approved_at``
    are preserved so the approval provenance survives retirement."""
    svc = PlaybookService(db_session)
    approver = uuid4()
    archiver = uuid4()  # a different auditor retires it
    assert approver != archiver
    pb = await svc.draft(_create(title="Provenance pb"), created_by=uuid4())
    await svc.approve(pb.id, approver_id=approver)
    archived = await svc.archive(pb.id, approver_id=archiver)
    assert archived.status == PlaybookStatus.ARCHIVED
    # Approval provenance preserved (NOT overwritten with the archiver).
    assert archived.approved_by == approver
    # The archiver is recorded distinctly.
    assert archived.archived_by == archiver
    assert archived.archived_at is not None


@pytest.mark.asyncio
async def test_reject_stamps_archiver_and_leaves_approval_unset(
    db_session: AsyncSession,
) -> None:
    """#76: reject declines a DRAFT (never approved) — it must not fabricate
    approval attribution. ``approved_by``/``approved_at`` stay None; the
    rejecter is recorded in ``archived_by``/``archived_at`` (reject ends in
    ARCHIVED, the same terminal state as archive)."""
    svc = PlaybookService(db_session)
    rejecter = uuid4()
    pb = await svc.draft(_create(title="Rejected draft"), created_by=uuid4())
    out = await svc.reject(pb.id, approver_id=rejecter, reason="dup")
    assert out.status == PlaybookStatus.ARCHIVED
    # A rejected draft was never approved — no fabricated approval attribution.
    assert out.approved_by is None
    assert out.approved_at is None
    # The rejecter is recorded as the one who archived it.
    assert out.archived_by == rejecter
    assert out.archived_at is not None


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


# --- gap 2: Board Program LEARN wiring --------------------------------------
# A playbook drafted through propose_playbook_drafts (Librarian) or
# propose_postmortem's playbook kind (Coroner) both carry created_by == the
# same fixed Auditor identity (that agent runs both programs). created_by
# alone can't tell them apart, so PlaybookService.draft() takes an explicit
# source_program kwarg the two direct-service call sites stamp, and
# approve/reject route a decision into THAT program's cycle only, never for
# a plain delivery-role draft_playbook draft (source_program=None).

_AUDITOR_ID = _foundation.AGENTS["auditor"].uuid


async def _seed_cycle(session: AsyncSession, program_key: str) -> None:
    session.add(
        BoardProgramCycleTable(
            program_key=program_key,
            exploration_task_id=None,
            opened_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def _cycle_row(session: AsyncSession, program_key: str) -> BoardProgramCycleTable:
    result = await session.execute(
        select(BoardProgramCycleTable).where(
            BoardProgramCycleTable.program_key == program_key
        )
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_approve_librarian_playbook_records_learn_decision(
    db_session: AsyncSession,
) -> None:
    await _seed_cycle(db_session, "librarian")
    svc = PlaybookService(db_session)
    pb = await svc.draft(
        _create(title="Retry a flaky migration"),
        created_by=_AUDITOR_ID,
        source_program="librarian",
    )
    await svc.approve(pb.id, approver_id=uuid4())

    row = await _cycle_row(db_session, "librarian")
    assert row.items_approved == 1
    decision = row.decisions[0]
    assert decision["verdict"] == "approved"
    assert decision["item_snapshot"]["title"] == "Retry a flaky migration"


@pytest.mark.asyncio
async def test_reject_librarian_playbook_records_learn_decision_with_reason(
    db_session: AsyncSession,
) -> None:
    await _seed_cycle(db_session, "librarian")
    svc = PlaybookService(db_session)
    pb = await svc.draft(
        _create(title="Duplicate of an existing playbook"),
        created_by=_AUDITOR_ID,
        source_program="librarian",
    )
    await svc.reject(pb.id, approver_id=uuid4(), reason="duplicate")

    row = await _cycle_row(db_session, "librarian")
    assert row.items_rejected == 1
    assert row.decisions[0]["verdict"] == "rejected"
    assert row.decisions[0]["reason"] == "duplicate"


@pytest.mark.asyncio
async def test_approve_plain_draft_does_not_record_learn(
    db_session: AsyncSession,
) -> None:
    """A playbook drafted by a delivery role (source_program=None, whatever
    created_by is) is not a board-program cycle item: approving it must
    never touch the librarian ledger, even when a cycle is open."""
    await _seed_cycle(db_session, "librarian")
    svc = PlaybookService(db_session)
    pb = await svc.draft(_create(title="Ordinary delivery draft"), created_by=uuid4())
    await svc.approve(pb.id, approver_id=uuid4())

    row = await _cycle_row(db_session, "librarian")
    assert row.items_approved == 0
    assert row.decisions == []


@pytest.mark.asyncio
async def test_approve_coroner_playbook_records_learn_against_coroner_not_librarian(
    db_session: AsyncSession,
) -> None:
    """The genuinely ambiguous case (live-verified regression): a
    Coroner-drafted playbook carries the EXACT SAME created_by as a
    Librarian-drafted one (the same fixed Auditor identity runs both
    programs), so created_by cannot discriminate. With both cycles open,
    approving a Coroner playbook must credit ONLY the coroner cycle; the
    librarian cycle (a real, live one open at the same time) must stay
    untouched. Before the source_program fix this landed on "librarian"
    instead, per DEFECT 1's live-DB observation."""
    await _seed_cycle(db_session, "coroner")
    await _seed_cycle(db_session, "librarian")
    svc = PlaybookService(db_session)
    pb = await svc.draft(
        _create(title="Verify venv freshness before gate"),
        created_by=_AUDITOR_ID,  # identical to the librarian test above
        source_program="coroner",
    )
    await svc.approve(pb.id, approver_id=uuid4())

    coroner_row = await _cycle_row(db_session, "coroner")
    librarian_row = await _cycle_row(db_session, "librarian")
    assert coroner_row.items_approved == 1
    assert coroner_row.decisions[0]["verdict"] == "approved"
    assert librarian_row.items_approved == 0
    assert librarian_row.decisions == []

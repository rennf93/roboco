"""PlaybookService — draft a curated procedure + the Auditor curation transitions.

A playbook is drafted by a delivery agent (status=draft), then the Auditor
approves it (draft -> approved, stamped) or rejects it (-> archived). Only
approved playbooks are embedded into the PLAYBOOKS RAG index (wired on approval
in a later step). The status is a plain string column; PlaybookStatus carries the
valid values here.
"""

from __future__ import annotations

import re
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from roboco.config import settings
from roboco.db.tables import PlaybookTable
from roboco.models.base import PlaybookStatus
from roboco.services.base import BaseService, ConflictError, NotFoundError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.models.playbook import PlaybookCreate

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 80


def _slugify(title: str) -> str:
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return slug[:_SLUG_MAX] or "playbook"


class PlaybookService(BaseService):
    """Draft + curate curated playbooks."""

    service_name: ClassVar[str] = "playbook"

    async def draft(self, data: PlaybookCreate, created_by: UUID) -> PlaybookTable:
        """Create a DRAFT playbook; slug is derived from the title (unique).

        The ``_get_by_slug`` pre-check is a fast-path for UX, not the
        authoritative guard: two concurrent same-title drafts both miss it
        (neither sees the other's uncommitted row), so the loser's flush hits
        the ``playbooks.slug`` UNIQUE constraint and raises ``IntegrityError``.
        The DB constraint is the real guard; isolating the insert in a savepoint
        and converting the ``IntegrityError`` into the same ``ConflictError``
        the pre-check raises closes the TOCTOU — the loser gets a clean conflict
        (409), not an unhandled 500. The savepoint also rolls back only the
        failed insert, leaving the caller's pending transaction usable.
        """
        slug = _slugify(data.title)
        if await self._get_by_slug(slug) is not None:
            raise ConflictError(
                f"Playbook with slug '{slug}' already exists",
                resource_type="playbook",
            )
        source_ids = [str(data.source_task_id)] if data.source_task_id else []
        playbook = PlaybookTable(
            title=data.title,
            slug=slug,
            problem=data.problem,
            procedure=data.procedure,
            tags=list(data.tags),
            team=data.team,
            scope=data.scope,
            source_task_ids=source_ids,
            status=PlaybookStatus.DRAFT.value,
            created_by=created_by,
        )
        self.session.add(playbook)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"Playbook with slug '{slug}' already exists",
                resource_type="playbook",
            ) from exc
        self.log.info("Playbook drafted", playbook_id=str(playbook.id), slug=slug)
        return playbook

    async def approve(self, playbook_id: UUID, approver_id: UUID) -> PlaybookTable:
        """Auditor approves a draft: draft -> approved, stamped.

        Flushes the status change ONLY — the RAG index write (``index_approved``)
        is a SEPARATE step the caller runs AFTER committing the status. The vector
        store writes through its own auto-committing pool connection, so indexing
        inline (before the status commit) would durably land an approved playbook
        in the corpus even if the status transaction rolled back — a divergence
        agents then surfaced in briefings.

        Retry: an APPROVED playbook whose ``indexed_ok`` is still False (the
        post-commit index write failed or never ran — e.g. an Ollama restart
        mid-approval-burst) is re-approvable. The status/provenance are NOT
        re-stamped on retry; the caller just re-runs ``index_approved``.
        """
        playbook = await self._get_or_raise(playbook_id)
        is_draft = playbook.status == PlaybookStatus.DRAFT.value
        is_approved_unindexed = (
            playbook.status == PlaybookStatus.APPROVED.value and not playbook.indexed_ok
        )
        if not (is_draft or is_approved_unindexed):
            already_indexed = playbook.status == PlaybookStatus.APPROVED.value
            raise ConflictError(
                f"Playbook {playbook_id} is {playbook.status}"
                f"{' (already indexed)' if already_indexed else ''}"
                " — only a draft (or an approved-but-unindexed playbook"
                " on retry) can be approved",
                resource_type="playbook",
            )
        if is_draft:
            playbook.status = PlaybookStatus.APPROVED.value
            playbook.approved_by = approver_id
            playbook.approved_at = datetime.now(UTC)
            playbook.indexed_ok = False
            playbook.indexed_at = None
        # Retry path: no status/provenance mutation — the caller re-indexes.
        await self.session.flush()
        self.log.info(
            "Playbook approved",
            playbook_id=str(playbook_id),
            retry=not is_draft,
        )
        return playbook

    async def archive(self, playbook_id: UUID, approver_id: UUID) -> PlaybookTable:
        """Auditor retires an APPROVED playbook: approved -> archived.

        The distinct curation transition from :meth:`reject`: ``reject``
        declines a DRAFT (never published); ``archive`` retires an APPROVED
        playbook already in circulation. Both end in ARCHIVED, but they start
        from different states, so each guards its own precondition. An ARCHIVED
        playbook is terminal — neither approve, reject, nor archive may touch
        it again. Like reject, the status flush is the only in-tx step; the
        post-commit ``unindex_playbook`` is the caller's separate step.
        """
        playbook = await self._get_or_raise(playbook_id)
        if playbook.status != PlaybookStatus.APPROVED.value:
            raise ConflictError(
                f"Playbook {playbook_id} is {playbook.status}, not approved — "
                "only an approved playbook can be archived",
                resource_type="playbook",
            )
        playbook.status = PlaybookStatus.ARCHIVED.value
        playbook.archived_by = approver_id
        playbook.archived_at = datetime.now(UTC)
        await self.session.flush()
        self.log.info("Playbook archived", playbook_id=str(playbook_id))
        return playbook

    async def index_approved(self, playbook: PlaybookTable) -> None:
        """Embed an approved playbook into the PLAYBOOKS RAG index (best-effort).

        Post-commit step: the caller commits the ``draft -> approved`` status
        change FIRST, then runs this so the index never leads the status
        transaction. Gated on ``org_memory_enabled`` so the feature is fully inert
        when off; a failure (e.g. the embedder is down) never blocks the approval.

        Stamps ``indexed_ok=True`` + ``indexed_at`` ONLY on a successful embed
        (mirroring ``index_journal_entry``'s ``if not result.success: return``
        guard) — a swallowed failure leaves the row APPROVED-but-unindexed for
        the startup reconcile to retry. The flush rides the caller's still-open
        transaction; the route/verb commits it after this returns.
        """
        if not settings.org_memory_enabled:
            return
        try:
            from roboco.services.optimal import get_optimal_service
            from roboco.services.optimal_brain.indexes.playbooks import (
                IndexPlaybookParams,
            )

            optimal = await get_optimal_service()
            result = await optimal.index_playbook(
                IndexPlaybookParams(
                    playbook_id=str(playbook.id),
                    title=playbook.title,
                    problem=playbook.problem,
                    procedure=playbook.procedure,
                    tags=list(playbook.tags or []),
                    team=playbook.team,
                    scope=playbook.scope,
                )
            )
        except Exception as exc:
            self.log.warning(
                "Playbook index-on-approve failed (best-effort)",
                playbook_id=str(playbook.id),
                error=str(exc),
            )
            return
        if result is not None and result.success:
            playbook.indexed_ok = True
            playbook.indexed_at = datetime.now(UTC)
            try:
                await self.session.flush()
            except Exception as exc:
                self.log.warning(
                    "Playbook indexed_ok stamp flush failed (best-effort)",
                    playbook_id=str(playbook.id),
                    error=str(exc),
                )

    async def reconcile_unindexed_approved(self) -> int:
        """Re-index APPROVED playbooks left ``indexed_ok=False`` by a failed
        post-commit embed (e.g. an Ollama restart mid-approval-burst).

        Best-effort: a per-playbook failure is logged and rolled back; the
        loop continues so one bad row never blocks the rest. Gated on
        ``org_memory_enabled`` (inert when the loop is off). Returns the count
        of playbooks successfully re-indexed.
        """
        if not settings.org_memory_enabled:
            return 0
        stmt = select(PlaybookTable).where(
            PlaybookTable.status == PlaybookStatus.APPROVED.value,
            PlaybookTable.indexed_ok.is_(False),
        )
        result = await self.session.execute(stmt)
        playbooks = list(result.scalars().all())
        if not playbooks:
            return 0
        self.log.info(
            "Playbook reconcile: re-indexing unindexed approved",
            count=len(playbooks),
        )
        count = 0
        for pb in playbooks:
            try:
                await self.index_approved(pb)
                # index_approved swallows embedder exceptions (best-effort),
                # so the durable signal of success is the indexed_ok flag it
                # stamps only on a successful embed. A row still False after
                # the call is a failure — rollback and let the next startup
                # retry it.
                if pb.indexed_ok:
                    await self.session.commit()
                    count += 1
                else:
                    await self.session.rollback()
            except Exception as exc:
                self.log.warning(
                    "Playbook reconcile: re-index failed (best-effort)",
                    playbook_id=str(pb.id),
                    error=str(exc),
                )
                with suppress(Exception):
                    await self.session.rollback()
        return count

    async def reject(
        self, playbook_id: UUID, approver_id: UUID, reason: str
    ) -> PlaybookTable:
        """Auditor rejects a playbook: -> archived (reason recorded in the log).

        Flushes the status change ONLY — ``unindex_playbook`` is a separate
        post-commit step (see ``approve`` for the ordering rationale).
        """
        playbook = await self._get_or_raise(playbook_id)
        if playbook.status != PlaybookStatus.DRAFT.value:
            raise ConflictError(
                f"Playbook {playbook_id} is {playbook.status}, not draft — "
                "only a draft can be rejected",
                resource_type="playbook",
            )
        playbook.status = PlaybookStatus.ARCHIVED.value
        playbook.archived_by = approver_id
        playbook.archived_at = datetime.now(UTC)
        await self.session.flush()
        self.log.info("Playbook rejected", playbook_id=str(playbook_id), reason=reason)
        return playbook

    async def unindex_playbook(self, playbook: PlaybookTable) -> None:
        """De-index a playbook from the PLAYBOOKS RAG index (best-effort).

        The mirror of :meth:`_index_approved`: a rejected/archived playbook that
        was previously approved+indexed must stop surfacing in agent briefings,
        so ``reject`` drops its chunks + tracking row. Gated on
        ``org_memory_enabled`` (inert when the loop is off) and best-effort (a
        failure never blocks the curation). Idempotent on a never-indexed draft.
        """
        if not settings.org_memory_enabled:
            return
        try:
            from roboco.services.optimal import get_optimal_service

            optimal = await get_optimal_service()
            await optimal.unindex_playbook(str(playbook.id))
        except Exception as exc:
            self.log.warning(
                "Playbook de-index-on-reject failed (best-effort)",
                playbook_id=str(playbook.id),
                error=str(exc),
            )

    async def list_drafts(self) -> list[PlaybookTable]:
        return await self._list_by_status(PlaybookStatus.DRAFT)

    async def list_approved(self, team: str | None = None) -> list[PlaybookTable]:
        stmt = select(PlaybookTable).where(
            PlaybookTable.status == PlaybookStatus.APPROVED.value
        )
        if team is not None:
            stmt = stmt.where(PlaybookTable.team == team)
        result = await self.session.execute(stmt.order_by(PlaybookTable.created_at))
        return list(result.scalars().all())

    async def _list_by_status(self, status: PlaybookStatus) -> list[PlaybookTable]:
        result = await self.session.execute(
            select(PlaybookTable)
            .where(PlaybookTable.status == status.value)
            .order_by(PlaybookTable.created_at)
        )
        return list(result.scalars().all())

    async def _get_by_slug(self, slug: str) -> PlaybookTable | None:
        result = await self.session.execute(
            select(PlaybookTable).where(PlaybookTable.slug == slug)
        )
        return result.scalar_one_or_none()

    async def _get_or_raise(self, playbook_id: UUID) -> PlaybookTable:
        result = await self.session.execute(
            select(PlaybookTable).where(PlaybookTable.id == playbook_id)
        )
        playbook = result.scalar_one_or_none()
        if playbook is None:
            raise NotFoundError("Playbook", str(playbook_id))
        return playbook


def get_playbook_service(session: AsyncSession) -> PlaybookService:
    """Construct a PlaybookService bound to ``session``."""
    return PlaybookService(session)

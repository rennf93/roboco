"""Repository for the revision-findings ledger (``task_review_findings``).

Append-only: ``insert_many`` adds one row per finding for a single producer
call (fail_review / pr_fail / request_changes / ceo_reject); ``mark_addressed``
/ ``mark_verified`` / ``mark_waived`` advance a row's status without ever
deleting or overwriting the original finding — history is the point (contrast
``ProjectConventionFindingTable``, which replaces per-task).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from roboco.db.tables import TaskReviewFindingTable
from roboco.services.repositories.base import BaseRepository

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.foundation.policy.content import Finding

STATUS_OPEN = "open"
STATUS_ADDRESSED = "addressed"
STATUS_VERIFIED = "verified"
STATUS_WAIVED = "waived"

_RESOLUTION_NOTE_CAP = 300


class ReviewFindingsRepository(BaseRepository[TaskReviewFindingTable]):
    """Repository for the append-only revision-findings ledger."""

    model = TaskReviewFindingTable
    model_name = "TaskReviewFinding"

    async def insert_many(
        self,
        *,
        task_id: UUID,
        origin: str,
        round: int,
        author_slug: str | None,
        findings: list[Finding],
    ) -> list[TaskReviewFindingTable]:
        """Insert one ledger row per finding, in order. Caller flushes/commits
        as part of its own unit of work (mirrors ``apply_structured_note``'s
        in-session mutation — no independent commit here)."""
        rows = [
            TaskReviewFindingTable(
                task_id=task_id,
                origin=origin,
                round=round,
                author_slug=author_slug,
                file=f.file,
                line=f.line,
                severity=f.severity.value,
                criterion=f.criterion,
                expected=f.expected,
                actual=f.actual,
                fix=f.fix,
                evidence=f.evidence,
            )
            for f in findings
        ]
        for row in rows:
            self.session.add(row)
        await self.session.flush()
        return rows

    async def list_for_task(
        self, task_id: UUID, *, status: str | None = None, limit: int = 500
    ) -> list[TaskReviewFindingTable]:
        """A task's findings, newest round first (ties broken newest-created)."""
        stmt = select(TaskReviewFindingTable).where(
            TaskReviewFindingTable.task_id == task_id
        )
        if status is not None:
            stmt = stmt.where(TaskReviewFindingTable.status == status)
        stmt = stmt.order_by(
            TaskReviewFindingTable.round.desc(),
            TaskReviewFindingTable.created_at.desc(),
        ).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def status_counts_for_task(self, task_id: UUID) -> list[tuple[str, str, int]]:
        """``(origin, status, count)`` aggregated over the task's FULL ledger.

        SQL GROUP BY, not a fetched-slice walk — the findings-route summary
        must stay correct past the list fetch's row cap.
        """
        stmt = (
            select(
                TaskReviewFindingTable.origin,
                TaskReviewFindingTable.status,
                func.count(),
            )
            .where(TaskReviewFindingTable.task_id == task_id)
            .group_by(TaskReviewFindingTable.origin, TaskReviewFindingTable.status)
        )
        result = await self.session.execute(stmt)
        return [(origin, status, count) for origin, status, count in result.all()]

    async def mark_addressed(
        self,
        task_id: UUID,
        finding_ref: str,
        *,
        commit: str | None,
        note: str | None,
    ) -> TaskReviewFindingTable | None:
        """Mark one OPEN finding addressed, matched by full id or an
        unambiguous 8-char prefix (what the rendered note/A2A body actually
        shows the agent — ``[F-<id8>]``). Scoped to ``task_id`` so an agent
        can never address another task's finding.

        Returns ``None`` (a no-op) on no match OR more than one match — an
        ambiguous ref is skipped rather than guessed, so the caller's own
        open-findings gate re-surfaces it by name. Never raises: a malformed
        resolution must not crash the caller (i_am_done).
        """
        rows = await self.list_for_task(task_id, status=STATUS_OPEN)
        ref = finding_ref.strip().lower()
        matches = [r for r in rows if str(r.id).lower().startswith(ref)]
        if len(matches) != 1:
            return None
        row = matches[0]
        row.status = STATUS_ADDRESSED
        row.addressed_by_commit = commit
        if note:
            row.resolution_note = note[:_RESOLUTION_NOTE_CAP]
        await self.session.flush()
        return row

    async def mark_verified(self, ids: list[UUID]) -> int:
        """Bulk-stamp ``verified`` on already-addressed findings by full id.

        Returns the number of rows updated (0 for an empty/unmatched list).
        """
        if not ids:
            return 0
        result = await self.session.execute(
            select(TaskReviewFindingTable).where(TaskReviewFindingTable.id.in_(ids))
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = STATUS_VERIFIED
        await self.session.flush()
        return len(rows)

    async def mark_waived(self, finding_id: UUID, note: str) -> bool:
        """PM/CEO waives one finding with a required note.

        Returns False when the finding does not exist; never raises.
        """
        row = await self.get(finding_id)
        if row is None:
            return False
        row.status = STATUS_WAIVED
        row.resolution_note = note[:_RESOLUTION_NOTE_CAP]
        await self.session.flush()
        return True

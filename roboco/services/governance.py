"""Governance report service — surfaces the quality-gate chain for a task.

Read-only aggregation that queries the audit log for gate transitions, reuses
``ReviewFindingsRepository`` for the revision-findings summary, queries
``ProjectConventionFindingTable`` for the conventions verdict, and reads
``TaskTable`` for status / revision_count. No writes — the governance report
is a point-in-time snapshot assembled on each request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from roboco.db.tables import (
    AuditLogTable,
    ProjectConventionFindingTable,
    TaskTable,
)
from roboco.models.base import TaskStatus
from roboco.services.repositories.review_findings import ReviewFindingsRepository

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.api.schemas.tasks import (
        GateStageResponse,
        TaskFindingsSummaryRow,
        TaskGovernanceReportResponse,
    )


# Audit event types that mark a gate evaluation or bounce.
_GATE_EVENTS = {
    "task.verifying": "self_verification",
    "task.awaiting_qa": "self_verification",
    "task.qa_fail": "qa",
    "task.awaiting_documentation": "qa",
    "task.pr_fail": "pr_gate",
    "task.awaiting_pm_review": "pr_gate",
    "task.request_changes": "pm_review",
    "task.awaiting_ceo_approval": "pm_review",
    "task.ceo_reject": "ceo_approval",
    "task.completed": "ceo_approval",
}

# Ordered gate names for the chain.
_GATE_ORDER = [
    "conventions",
    "self_verification",
    "qa",
    "pr_gate",
    "pm_review",
    "ceo_approval",
]


class GovernanceService:
    """Assembles the governance report for a single task."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_report(self, task_id: UUID) -> TaskGovernanceReportResponse | None:
        """Build the governance report, or ``None`` if the task doesn't exist."""
        task = await self._session.get(TaskTable, task_id)
        if task is None:
            return None

        gate_chain = await self._build_gate_chain(task_id, task)
        findings_summary = await self._findings_summary(task_id)
        block_count, warn_count = await self._conventions_verdict(task_id)

        from roboco.api.schemas.tasks import TaskGovernanceReportResponse

        return TaskGovernanceReportResponse(
            task_id=str(task.id),
            task_status=(
                task.status.value
                if isinstance(task.status, TaskStatus)
                else str(task.status)
            ),
            revision_count=task.revision_count,
            gate_chain=gate_chain,
            findings_summary=findings_summary,
            conventions_block_count=block_count,
            conventions_warn_count=warn_count,
        )

    @staticmethod
    def _gate_events_from_audit(
        events: list[AuditLogTable],
    ) -> dict[str, tuple[str, datetime | None]]:
        """Map each gate to its latest audit event.

        Bounce events (qa_fail, pr_fail, request_changes, ceo_reject) mark the
        gate as failed; advancement events mark it as passed.
        """
        gate_events: dict[str, tuple[str, datetime | None]] = {}
        for ev in events:
            gate = _GATE_EVENTS.get(ev.event_type)
            if gate is None:
                continue
            bounced = ev.event_type.endswith("_fail") or ev.event_type in (
                "task.request_changes",
                "task.ceo_reject",
            )
            gate_events[gate] = ("failed" if bounced else "passed", ev.timestamp)
        return gate_events

    async def _build_gate_chain(
        self, task_id: UUID, task: TaskTable
    ) -> list[GateStageResponse]:
        """Extract the ordered gate-chain stages from audit events + task fields."""
        # Fetch the task's audit events ordered by timestamp.
        stmt = (
            select(AuditLogTable)
            .where(AuditLogTable.target_id == task.id)
            .order_by(AuditLogTable.timestamp)
        )
        result = await self._session.execute(stmt)
        events = list(result.scalars().all())

        gate_events = self._gate_events_from_audit(events)

        # Conventions gate: no audit event — derive from convention findings.
        block_count, warn_count = await self._conventions_verdict(task_id)
        if block_count > 0:
            gate_events["conventions"] = ("failed", None)
        elif task.status not in (
            TaskStatus.PENDING,
            TaskStatus.CLAIMED,
            TaskStatus.IN_PROGRESS,
        ):
            # Task progressed past development — conventions gate was checked
            # (i_am_done runs the conventions validator) and no blocks found.
            gate_events["conventions"] = ("passed", None)

        # Self-verification: also reflected by task.self_verified.
        if task.self_verified and "self_verification" not in gate_events:
            gate_events["self_verification"] = ("passed", None)

        # QA: also reflected by task.qa_verified.
        if task.qa_verified and "qa" not in gate_events:
            gate_events["qa"] = ("passed", None)

        from roboco.api.schemas.tasks import GateStageResponse

        chain: list[GateStageResponse] = []
        for gate_name in _GATE_ORDER:
            if gate_name in gate_events:
                status_str, ts = gate_events[gate_name]
                detail = self._gate_detail(
                    gate_name, status_str, block_count, warn_count
                )
                chain.append(
                    GateStageResponse(
                        gate=gate_name,
                        status=status_str,
                        timestamp=ts,
                        detail=detail,
                    )
                )
            else:
                chain.append(
                    GateStageResponse(
                        gate=gate_name,
                        status="not_reached",
                        timestamp=None,
                        detail=None,
                    )
                )
        return chain

    @staticmethod
    def _gate_detail(
        gate: str,
        status: str,
        block_count: int,
        warn_count: int,
    ) -> str | None:
        """Human-readable detail for a gate stage."""
        if gate == "conventions":
            if status == "failed":
                return f"{block_count} block finding(s)"
            if status == "passed" and warn_count > 0:
                return f"{warn_count} warn finding(s)"
            return None
        if status == "failed":
            return "bounced"
        return None

    async def _findings_summary(self, task_id: UUID) -> list[TaskFindingsSummaryRow]:
        """Reuse ReviewFindingsRepository.status_counts_for_task + the
        findings_summary schema helper."""
        from roboco.api.schemas.tasks import findings_summary

        repo = ReviewFindingsRepository(self._session)
        counts = await repo.status_counts_for_task(task_id)
        return findings_summary(counts)

    async def _conventions_verdict(self, task_id: UUID) -> tuple[int, int]:
        """Count block / warn convention findings for the task."""
        stmt = (
            select(
                ProjectConventionFindingTable.level,
                func.count(),
            )
            .where(ProjectConventionFindingTable.task_id == task_id)
            .group_by(ProjectConventionFindingTable.level)
        )
        result = await self._session.execute(stmt)
        block = 0
        warn = 0
        for level, count in result.all():
            if level == "block":
                block = count
            elif level == "warn":
                warn = count
        return block, warn


def get_governance_service(session: AsyncSession) -> GovernanceService:
    """Get a GovernanceService instance."""
    return GovernanceService(session)

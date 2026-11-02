"""
Release Route Helpers

Route-glue helpers backing roboco/api/routes/release.py.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException, status

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.release import (
    ReleaseCiVerdictsResponse,
    ReleaseGapModel,
    ReleaseMemberTaskModel,
    ReleaseProposalResponse,
    ReleaseReportModel,
    ReleaseVerificationResponse,
    member_ci_verdict_to_model,
    member_receipt_to_model,
)
from roboco.foundation.policy.content import markers
from roboco.services.release_proposal import (
    get_release_proposal_service,
    is_approve_in_flight,
    member_task_ids_for_proposal,
)
from roboco.services.release_verification import (
    read_release_member_ci_verdicts,
    read_release_member_receipts,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on release proposals")


def _status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


async def _to_response(
    task: "TaskTable", db: "AsyncSession"
) -> ReleaseProposalResponse:
    report = markers.get_release_report(task) or {}
    outcome = markers.get_release_execute_outcome(task)
    member_tasks = await member_task_ids_for_proposal(db, task)
    return ReleaseProposalResponse(
        task_id=str(task.id),
        title=task.title,
        status=_status_value(task),
        required_changes=markers.get_release_required_changes(task),
        execute_status=outcome[0] if outcome else None,
        execute_detail=outcome[1] if outcome else None,
        execute_in_flight=is_approve_in_flight(UUID(str(task.id))),
        report=ReleaseReportModel(
            proposed_version=report.get("proposed_version", ""),
            bump_kind=report.get("bump_kind", ""),
            change_summary=report.get("change_summary", []),
            drafted_changelog=report.get("drafted_changelog", ""),
            version_bump_plan=report.get("version_bump_plan", []),
            gaps=[ReleaseGapModel(**gap) for gap in report.get("gaps", [])],
            migration_notes=report.get("migration_notes", []),
            gate_state=report.get("gate_state", "unknown"),
        ),
        member_task_ids=[ReleaseMemberTaskModel(**m) for m in member_tasks],
    )


async def open_proposal_or_404(db: "AsyncSession") -> "TaskTable":
    """The single held release proposal, or the 404 that says there is none."""
    task = await get_release_proposal_service(db).open_proposal()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No open release proposal"
        )
    return task


async def release_verification_response(
    task: "TaskTable", db: "AsyncSession"
) -> ReleaseVerificationResponse:
    """Every member task's receipt for ``task``, as the rollup's response."""
    receipts = await read_release_member_receipts(db, task)
    return ReleaseVerificationResponse(
        proposal_task_id=str(task.id),
        members=[member_receipt_to_model(r) for r in receipts],
    )


async def release_ci_verdicts_response(
    task: "TaskTable", db: "AsyncSession"
) -> ReleaseCiVerdictsResponse:
    """Every member task's PR CI verdict for ``task``, as one response."""
    verdicts = await read_release_member_ci_verdicts(db, task)
    return ReleaseCiVerdictsResponse(
        proposal_task_id=str(task.id),
        verdicts=[member_ci_verdict_to_model(v) for v in verdicts],
    )

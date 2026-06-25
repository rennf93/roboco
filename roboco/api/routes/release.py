"""Release-manager API — the CEO approves or rejects a held release proposal.

CEO-only. ``GET /proposal`` renders the held proposal + its readiness report;
``approve`` runs the fail-closed executor; ``reject`` records required changes and
keeps the proposal held. Nothing here publishes without the CEO's explicit POST.
"""

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.release import (
    ReleaseExecuteResponse,
    ReleaseGapModel,
    ReleaseProposalResponse,
    ReleaseRejectRequest,
    ReleaseReportModel,
)
from roboco.foundation.policy.content import markers
from roboco.models import AgentRole
from roboco.services.release_proposal import get_release_proposal_service

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.db.tables import TaskTable

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    if agent.role != AgentRole.CEO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the CEO may view or act on release proposals",
        )


def _status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def _to_response(task: "TaskTable") -> ReleaseProposalResponse:
    report = markers.get_release_report(task) or {}
    return ReleaseProposalResponse(
        task_id=str(task.id),
        title=task.title,
        status=_status_value(task),
        required_changes=markers.get_release_required_changes(task),
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
    )


@router.get("/proposal", response_model=ReleaseProposalResponse)
async def get_release_proposal(
    db: DbSession, agent: CurrentAgentContext
) -> ReleaseProposalResponse:
    """The single held release proposal awaiting the CEO (404 when none)."""
    _require_ceo(agent)
    task = await get_release_proposal_service(db).open_proposal()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No open release proposal"
        )
    return _to_response(task)


@router.post("/proposal/approve", response_model=ReleaseExecuteResponse)
async def approve_release_proposal(
    db: DbSession, agent: CurrentAgentContext
) -> ReleaseExecuteResponse:
    """Approve the held proposal → run the fail-closed executor."""
    _require_ceo(agent)
    svc = get_release_proposal_service(db)
    task = await svc.open_proposal()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No open release proposal"
        )
    result = await svc.approve(cast("UUID", task.id))
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Proposal has no stored readiness report",
        )
    await db.commit()
    return ReleaseExecuteResponse(
        status=result.status,
        version=result.version,
        files_changed=result.files_changed,
        commit_sha=result.commit_sha,
        release_url=result.release_url,
        detail=result.detail,
    )


@router.post("/proposal/reject", response_model=ReleaseProposalResponse)
async def reject_release_proposal(
    data: ReleaseRejectRequest, db: DbSession, agent: CurrentAgentContext
) -> ReleaseProposalResponse:
    """Reject the held proposal with required changes; it stays held for revision."""
    _require_ceo(agent)
    svc = get_release_proposal_service(db)
    task = await svc.open_proposal()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No open release proposal"
        )
    revised = await svc.reject(cast("UUID", task.id), data.required_changes)
    if revised is None:  # pragma: no cover - open_proposal already guaranteed it
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No open release proposal"
        )
    await db.commit()
    return _to_response(revised)

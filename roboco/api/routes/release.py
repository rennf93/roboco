"""Release-manager API — the CEO approves or rejects a held release proposal.

CEO-only. ``GET /proposal`` renders the held proposal + its readiness report;
``approve`` runs the fail-closed executor; ``reject`` records required changes and
keeps the proposal held. Nothing here publishes without the CEO's explicit POST.
"""

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.release import (
    ReleaseExecuteResponse,
    ReleaseGapModel,
    ReleaseProposalResponse,
    ReleaseRejectRequest,
    ReleaseReportModel,
)
from roboco.foundation.policy.content import markers
from roboco.security import guard_deco
from roboco.services.release_proposal import (
    dispatch_approve,
    get_release_proposal_service,
)

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.db.tables import TaskTable

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on release proposals")


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


@router.post(
    "/proposal/approve",
    response_model=ReleaseExecuteResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.block_clouds()
@guard_deco.usage_monitor(max_calls=30, window=3600)
async def approve_release_proposal(
    db: DbSession, agent: CurrentAgentContext
) -> ReleaseExecuteResponse:
    """Approve the held proposal → dispatch the fail-closed executor async.

    The execute is a ~40min clone→gate→CI→publish pipeline; running it inline
    would 504 at nginx (the single :3000 entry point, ~60s read timeout) before
    it finished, so the CEO's approve always appeared to fail even when the
    release succeeded server-side. The route dispatches the execute in a
    background task with a fresh session and returns 202 immediately; the panel
    polls ``GET /proposal`` to observe the final status (COMPLETED on
    published/already_published, else the proposal stays open for retry). A
    second click is refused by the Redis mutex (``already_in_progress``).
    """
    _require_ceo(agent)
    svc = get_release_proposal_service(db)
    task = await svc.open_proposal()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No open release proposal"
        )
    # Materialize the proposal for the background session (a no-op in prod,
    # where the release-manager engine already committed it; tests seed it only
    # flushed into the request session).
    await db.commit()
    factory = async_sessionmaker(
        bind=db.bind, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    dispatch_approve(cast("UUID", task.id), factory)
    return ReleaseExecuteResponse(
        status="accepted",
        version="",
        files_changed=[],
        commit_sha=None,
        release_url=None,
        detail=(
            "Release execute dispatched in the background; poll"
            " /api/release/proposal for the final status."
        ),
    )


@router.post("/proposal/reject", response_model=ReleaseProposalResponse)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
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

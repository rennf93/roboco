"""Release-manager API — the CEO approves or rejects a held release proposal.

CEO-only. ``GET /proposal`` renders the held proposal + its readiness report;
``approve`` runs the fail-closed executor; ``reject`` records required changes and
cancels the proposal (freeing the one-open dedup for a fresh re-assessment).
Nothing here publishes without the CEO's explicit POST.
"""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.release import (
    ReleaseExecuteResponse,
    ReleaseProposalResponse,
    ReleaseRejectRequest,
)
from roboco.api.utils.release import _require_ceo, _to_response
from roboco.security import guard_deco
from roboco.services.release_proposal import (
    dispatch_approve,
    get_release_proposal_service,
)

router = APIRouter()


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
    """Reject the held proposal with required changes; it is cancelled so the
    release manager re-assesses and may originate a fresh proposal next cycle."""
    _require_ceo(agent)
    svc = get_release_proposal_service(db)
    task = await svc.open_proposal()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No open release proposal"
        )
    revised = await svc.reject(cast("UUID", task.id), data.required_changes)
    if revised is None:
        # A concurrent approve is mid-execute (holds the release mutex) or
        # Redis is unreachable — reject fails closed rather than racing it.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Reject refused: a release approve is in progress or Redis is"
                " unavailable. Retry once it clears."
            ),
        )
    await db.commit()
    return _to_response(revised)

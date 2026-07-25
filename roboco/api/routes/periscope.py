"""Periscope (Board Program) engine API — the CEO reads filed market briefs.
CEO-only throughout. Read-only: a brief is a report, not a queue item — there
is no approve/reject route here, unlike roadmap/pest_control. Mirrors
``roboco.api.routes.pest_control``'s CEO-gating shape.
"""

from typing import TYPE_CHECKING

from fastapi import APIRouter

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.periscope import MarketBriefFindingResponse, MarketBriefResponse
from roboco.foundation.policy.content import markers
from roboco.services.task import get_task_service

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view the Periscope market-briefs list")


def _to_response(task: "TaskTable") -> MarketBriefResponse | None:
    payload = markers.get_market_brief(task)
    if payload is None:
        return None
    findings = [MarketBriefFindingResponse(**f) for f in payload.get("findings", [])]
    return MarketBriefResponse(
        task_id=str(task.id),
        title=task.title,
        completed_at=task.updated_at.isoformat() if task.updated_at else None,
        headline=payload.get("headline", ""),
        findings=findings,
        threats=payload.get("threats", []),
        opportunities=payload.get("opportunities", []),
        positioning_note=payload.get("positioning_note", ""),
    )


@router.get("/briefs", response_model=list[MarketBriefResponse])
async def list_market_briefs(
    db: DbSession, agent: CurrentAgentContext
) -> list[MarketBriefResponse]:
    """Recent filed market briefs, newest-first. A completed Periscope
    exploration without a marker (shouldn't happen — the verb always sets
    one before completing) is omitted rather than rendered blank."""
    _require_ceo(agent)
    tasks = await get_task_service(db).list_periscope_briefs()
    return [r for t in tasks if (r := _to_response(t)) is not None]

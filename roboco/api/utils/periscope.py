"""
Periscope Route Helpers

Route-glue helpers backing roboco/api/routes/periscope.py.
"""

from typing import TYPE_CHECKING

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.periscope import MarketBriefFindingResponse, MarketBriefResponse
from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


def require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(
        agent.role, action="view or act on the Periscope market-briefs list"
    )


def to_response(task: "TaskTable") -> MarketBriefResponse | None:
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

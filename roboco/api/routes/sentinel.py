"""Sentinel (Board Program) engine API — the CEO reads filed quality reports.
CEO-only throughout. Read-only: a report is a report, not a queue item — there
is no approve/reject route here, unlike roadmap/pest_control. Mirrors
``roboco.api.routes.periscope``'s CEO-gating shape.
"""

from typing import TYPE_CHECKING

from fastapi import APIRouter

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.sentinel import QualityReportItemResponse, QualityReportResponse
from roboco.foundation.policy.content import markers
from roboco.services.task import get_task_service

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view the Sentinel quality-reports list")


def _to_response(task: "TaskTable") -> QualityReportResponse | None:
    payload = markers.get_quality_report(task)
    if payload is None:
        return None
    items = [QualityReportItemResponse(**i) for i in payload.get("items", [])]
    return QualityReportResponse(
        task_id=str(task.id),
        title=task.title,
        completed_at=task.updated_at.isoformat() if task.updated_at else None,
        headline=payload.get("headline", ""),
        items=items,
        overall_assessment=payload.get("overall_assessment", ""),
    )


@router.get("/reports", response_model=list[QualityReportResponse])
async def list_quality_reports(
    db: DbSession, agent: CurrentAgentContext
) -> list[QualityReportResponse]:
    """Recent filed quality reports, newest-first. A completed Sentinel
    exploration without a marker (shouldn't happen — the verb always sets
    one before completing) is omitted rather than rendered blank."""
    _require_ceo(agent)
    tasks = await get_task_service(db).list_sentinel_reports()
    return [r for t in tasks if (r := _to_response(t)) is not None]

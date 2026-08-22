"""
Sentinel Route Helpers

Route-glue helpers backing roboco/api/routes/sentinel.py.
"""

from typing import TYPE_CHECKING

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.sentinel import (
    QualityReportItemResponse,
    QualityReportResponse,
)
from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(
        agent.role, action="view or act on the Sentinel quality-reports list"
    )


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

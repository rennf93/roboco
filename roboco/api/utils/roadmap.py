"""
Roadmap Route Helpers

Route-glue helpers backing roboco/api/routes/roadmap.py.
"""

from typing import TYPE_CHECKING

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.roadmap import RoadmapCycleResponse, RoadmapItemResponse
from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


def require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on the roadmap queue")


def status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def to_response(task: "TaskTable") -> RoadmapCycleResponse:
    payload = markers.get_roadmap_cycle(task) or {}
    items = [RoadmapItemResponse(**item) for item in payload.get("items", [])]
    return RoadmapCycleResponse(
        task_id=str(task.id),
        title=task.title,
        status=status_value(task),
        goal=str(payload.get("goal") or ""),
        items=items,
    )

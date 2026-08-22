"""
Dogfood Route Helpers

Route-glue helpers backing roboco/api/routes/dogfood.py.
"""

from typing import TYPE_CHECKING

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.dogfood import DogfoodCycleResponse, FrictionFixItemResponse
from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on the dogfood queue")


def _status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def _to_response(task: "TaskTable") -> DogfoodCycleResponse:
    payload = markers.get_friction_fixes(task) or {}
    items = [FrictionFixItemResponse(**item) for item in payload.get("items", [])]
    return DogfoodCycleResponse(
        task_id=str(task.id),
        title=task.title,
        status=_status_value(task),
        items=items,
    )

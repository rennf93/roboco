"""
Spackle Route Helpers

Route-glue helpers backing roboco/api/routes/spackle.py.
"""

from typing import TYPE_CHECKING

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.spackle import GapFillItemResponse, SpackleCycleResponse
from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on the spackle queue")


def _status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def _to_response(task: "TaskTable") -> SpackleCycleResponse:
    payload = markers.get_gap_fill(task) or {}
    items = [GapFillItemResponse(**item) for item in payload.get("items", [])]
    return SpackleCycleResponse(
        task_id=str(task.id),
        title=task.title,
        status=_status_value(task),
        items=items,
    )

"""
Scales Route Helpers

Route-glue helpers backing roboco/api/routes/scales.py.
"""

from typing import TYPE_CHECKING

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.scales import RebalanceCycleResponse, RebalanceItemResponse
from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


def require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on the Scales queue")


def status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def to_response(task: "TaskTable") -> RebalanceCycleResponse:
    payload = markers.get_rebalance_plan(task) or {}
    items = [RebalanceItemResponse(**item) for item in payload.get("items", [])]
    return RebalanceCycleResponse(
        task_id=str(task.id),
        title=task.title,
        status=status_value(task),
        items=items,
    )

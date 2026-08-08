"""
Mirror Route Helpers

Route-glue helpers backing roboco/api/routes/mirror.py.
"""

from typing import TYPE_CHECKING

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.mirror import MessagingFixItemResponse, MirrorCycleResponse
from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


def require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on the mirror queue")


def status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def to_response(task: "TaskTable") -> MirrorCycleResponse:
    payload = markers.get_messaging_fixes(task) or {}
    items = [MessagingFixItemResponse(**item) for item in payload.get("items", [])]
    return MirrorCycleResponse(
        task_id=str(task.id),
        title=task.title,
        status=status_value(task),
        items=items,
    )

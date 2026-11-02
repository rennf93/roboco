"""
Maintenance Pause Route Helpers

Route-glue helpers backing roboco/api/routes/maintenance_pause.py.
"""

from typing import TYPE_CHECKING

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.maintenance_pause import MaintenancePauseResponse

if TYPE_CHECKING:
    from datetime import datetime

    from roboco.foundation.policy.maintenance_pause import PauseState


def require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or change the maintenance pause")


def to_response(
    state: "PauseState", *, read_degraded_since: "datetime | None" = None
) -> MaintenancePauseResponse:
    return MaintenancePauseResponse(
        scope=state.scope.value,
        paused=state.paused,
        paused_by=state.paused_by,
        paused_at=state.paused_at.isoformat() if state.paused_at else None,
        reason=state.reason,
        expires_at=state.expires_at.isoformat() if state.expires_at else None,
        read_degraded_since=(
            read_degraded_since.isoformat() if read_degraded_since else None
        ),
    )

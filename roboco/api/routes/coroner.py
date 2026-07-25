"""Coroner (Board Program) engine API — read-only Postmortems list.

Unlike Pest Control/Roadmap there is nothing here for the CEO to approve or
reject: a postmortem completes atomically the moment the Auditor calls
``propose_postmortem`` (spec §4). This route just lists what Coroner has
already found. CEO-only, mirroring every other Board Program surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.coroner import PostmortemResponse
from roboco.foundation.policy.content import markers
from roboco.services.task import get_task_service

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view the Coroner postmortems list")


def _to_response(task: TaskTable) -> PostmortemResponse:
    incident = markers.get_coroner_incident(task) or {}
    postmortem = markers.get_coroner_postmortem(task) or {}
    process_change = postmortem.get("process_change") or {}
    return PostmortemResponse(
        task_id=str(task.id),
        title=task.title,
        completed_at=task.updated_at.isoformat() if task.updated_at else None,
        incident_task_id=incident.get("incident_task_id"),
        incident_kind=incident.get("kind"),
        incident_title=incident.get("title"),
        incident_summary=postmortem.get("incident_summary"),
        root_cause=postmortem.get("root_cause"),
        failed_stage=postmortem.get("failed_stage"),
        process_change_kind=process_change.get("kind"),
        process_change_description=process_change.get("description"),
        playbook_id=postmortem.get("playbook_id"),
    )


@router.get("/postmortems", response_model=list[PostmortemResponse])
async def list_postmortems(
    db: DbSession, agent: CurrentAgentContext
) -> list[PostmortemResponse]:
    """Every completed Coroner postmortem, newest first."""
    _require_ceo(agent)
    tasks = await get_task_service(db).list_completed_coroner_postmortems()
    return [_to_response(t) for t in tasks]

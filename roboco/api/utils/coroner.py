"""
Coroner Route Helpers

Route-glue helpers backing roboco/api/routes/coroner.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.coroner import PostmortemResponse
from roboco.foundation.policy.content import markers
from roboco.services.coroner_service import PLAYBOOK_KIND

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on the Coroner postmortems list")


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
        # A playbook-kind change already drafted into the playbook queue at
        # propose time — there is nothing to decide, but the stored status
        # stays "proposed", which left the panel rendering approve/dismiss
        # buttons that both verbs refuse forever. Derive the terminal status
        # the panel's contract expects instead.
        process_change_status=(
            "not_applicable"
            if process_change.get("kind") == PLAYBOOK_KIND
            else process_change.get("status", "proposed")
        ),
        process_change_reject_reason=process_change.get("reject_reason"),
        process_change_materialized_task_id=process_change.get("materialized_task_id"),
    )

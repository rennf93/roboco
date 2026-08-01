"""Schemas for the Coroner (Board Program) engine's CEO surface.

A postmortem completes atomically at ``propose_postmortem`` time — the
EXPLORATION TASK has no per-item CEO decision to wait on — but its single
``process_change`` still carries its own proposed/approved/rejected status
for the CEO to decide on afterward (unless its kind is "playbook", already
routed into the playbook curation queue: status "not_applicable"). Unlike
Periscope/Sentinel there is no item id — a postmortem is one process change,
not a list (``roboco.services.coroner_engine``'s own docstring), so the
action routes key on the task id alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from roboco.foundation.policy.content import markers
from roboco.services.coroner_service import PLAYBOOK_KIND

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


class PostmortemResponse(BaseModel):
    """One completed Coroner postmortem — the incident ref + the Auditor's
    authored findings, both read off the exploration task's markers."""

    task_id: str
    title: str
    completed_at: str | None
    incident_task_id: str | None
    incident_kind: str | None
    incident_title: str | None
    incident_summary: str | None
    root_cause: str | None
    failed_stage: str | None
    process_change_kind: str | None
    process_change_description: str | None
    playbook_id: str | None
    # Defaults cover a postmortem authored before this feature shipped,
    # whose stored process_change carries none of these three keys.
    process_change_status: str = "proposed"
    process_change_reject_reason: str | None = None
    process_change_materialized_task_id: str | None = None


class ProcessChangeRejectRequest(BaseModel):
    """The CEO's reason for dismissing a postmortem's process change."""

    reason: str = Field(..., min_length=4)


class ProcessChangeActionResponse(BaseModel):
    """The outcome of an approve/reject call on a postmortem's process
    change."""

    status: str
    materialized_task_id: str | None = None
    detail: str


def task_to_postmortem_response(task: TaskTable) -> PostmortemResponse:
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

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

from pydantic import BaseModel, Field


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

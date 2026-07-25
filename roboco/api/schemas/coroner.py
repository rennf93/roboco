"""Schemas for the Coroner (Board Program) engine's read-only CEO surface.

Unlike Pest Control/Roadmap there is no approve/reject action here — a
postmortem completes atomically at ``propose_postmortem`` time (spec §4:
"report asymmetry — no per-item CEO decision"), so this is a plain list.
"""

from __future__ import annotations

from pydantic import BaseModel


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

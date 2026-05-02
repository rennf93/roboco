"""Build the `evidence` and `context_briefing` blocks for verb responses.

`evidence` is task-scoped: PR + commits + files + journal highlights.
`context_briefing` is agent-scoped: unread A2As, mentions, notifications,
task metadata gaps, recent team activity, blockers in lane.

This module is pure: it takes already-fetched lists and assembles them.
The choreographer queries the data via existing services and passes it in.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

BRIEFING_LIST_CAP = 10


@dataclass(frozen=True)
class EvidencePayload:
    pr_number: int | None
    pr_url: str | None
    pr_diff_summary: str | None
    commits: list[dict[str, Any]]
    files_changed: list[str]
    dev_summary: str | None
    journal_highlights: list[dict[str, Any]]
    acceptance_criteria_status: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BriefingInputs:
    """All agent-scoped context collected before assembling `context_briefing`."""

    unread_a2a: list[dict[str, Any]]
    unread_mentions: list[dict[str, Any]]
    pending_notifications: list[dict[str, Any]]
    task_metadata_gaps: list[str]
    recent_team_activity: list[dict[str, Any]]
    blockers_in_my_lane: list[dict[str, Any]]


def build_evidence_for_task(
    task: Any,
    *,
    journal_highlights: list[dict[str, Any]],
    files_changed: list[str],
    pr_diff_summary: str | None = None,
) -> EvidencePayload:
    """Compose an EvidencePayload from a Task model + supplemental data."""
    return EvidencePayload(
        pr_number=task.pr_number,
        pr_url=task.pr_url,
        pr_diff_summary=pr_diff_summary,
        commits=list(task.commits or []),
        files_changed=list(files_changed),
        dev_summary=task.dev_notes,
        journal_highlights=list(journal_highlights),
        acceptance_criteria_status=list(task.acceptance_criteria_status or []),
    )


def build_context_briefing(inputs: BriefingInputs) -> dict[str, Any]:
    """Compose the context_briefing dict; caps each list at BRIEFING_LIST_CAP items."""
    return {
        "unread_a2a": inputs.unread_a2a[:BRIEFING_LIST_CAP],
        "unread_mentions": inputs.unread_mentions[:BRIEFING_LIST_CAP],
        "pending_notifications": inputs.pending_notifications[:BRIEFING_LIST_CAP],
        "task_metadata_gaps": list(inputs.task_metadata_gaps),
        "recent_team_activity": inputs.recent_team_activity[:BRIEFING_LIST_CAP],
        "blockers_in_my_lane": inputs.blockers_in_my_lane[:BRIEFING_LIST_CAP],
    }

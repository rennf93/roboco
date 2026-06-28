"""Build the `evidence` and `context_briefing` blocks for verb responses.

`evidence` is task-scoped: PR + commits + files + journal highlights.
`context_briefing` is agent-scoped: unread A2As, mentions, notifications,
task metadata gaps, recent team activity, blockers in lane.

This module is pure: it takes already-fetched lists and assembles them.
The choreographer queries the data via existing services and passes it in.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    # Architectural-conventions validator findings on the changed files, so QA
    # can flag a misplaced definition / suppression. Empty when the subsystem
    # is off; a single ``could_not_run`` entry surfaces a fail-loud explicitly.
    convention_findings: list[dict[str, Any]] = field(default_factory=list)

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
    # Prior-work digest for the briefed task (None when there is no task in
    # scope or no prior work to resume from). Pushed so a freshly spawned or
    # respawned agent picks up where the previous worker left off instead of
    # re-exploring the codebase from cold.
    task_handoff: dict[str, Any] | None = None
    # Compact company charter (north star + objectives + operating policy), or
    # None when unset — injected so every agent's work is goal-aware.
    company_goals: dict[str, Any] | None = None


def build_evidence_for_task(
    task: Any,
    *,
    journal_highlights: list[dict[str, Any]],
    files_changed: list[str],
    pr_diff_summary: str | None = None,
    convention_findings: list[dict[str, Any]] | None = None,
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
        convention_findings=list(convention_findings or []),
    )


def _typed(value: Any, expected: type | tuple[type, ...], default: Any) -> Any:
    """Return ``value`` only when it is the expected type, else ``default``.

    Keeps every handoff field serialisable: a bare mock or unexpected attribute
    type degrades to a safe default rather than leaking a non-JSON object.
    """
    return value if isinstance(value, expected) else default


def build_task_handoff(
    task: Any, journal_highlights: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Compose a compact prior-work digest for the briefed task.

    Returns ``None`` when there is no task or no prior work worth resuming
    from, so the briefing only carries a handoff when one genuinely exists.
    DB-only by design — no git diff — so it is cheap enough to attach to
    every task-scoped briefing.
    """
    if task is None:
        return None
    commits = _typed(task.commits, list, [])
    acceptance = _typed(task.acceptance_criteria_status, list, [])
    highlights = _typed(journal_highlights, list, [])
    pr_number = _typed(task.pr_number, int, None)
    dev_summary = _typed(task.dev_notes, str, None)
    # Upstream dependencies that completed and were cleared — present only on a
    # just-unblocked task, so the revived dependent knows what it can build on.
    completed_deps = _typed(getattr(task, "completed_dependency_ids", None), list, [])
    # The persisted in-path PR-review gate verdict + concrete issues.
    # ``pr_fail`` writes ``notes_structured.pr_review``; surfacing it here puts
    # the concrete issues in every PM briefing so a respawned PM doesn't
    # re-submit the same PR blind.
    pr_review = _extract_pr_review(getattr(task, "notes_structured", None))
    has_prior = bool(
        commits
        or acceptance
        or highlights
        or pr_number is not None
        or dev_summary
        or completed_deps
        or pr_review is not None
    )
    if not has_prior:
        return None
    handoff: dict[str, Any] = {
        "pr_number": pr_number,
        "pr_url": _typed(task.pr_url, str, None),
        "branch_name": _typed(task.branch_name, str, None),
        "commit_count": len(commits),
        "recent_commits": commits[-BRIEFING_LIST_CAP:],
        "dev_summary": dev_summary,
        "acceptance_criteria_status": acceptance[:BRIEFING_LIST_CAP],
        "journal_highlights": highlights[:BRIEFING_LIST_CAP],
        "completed_dependency_ids": [
            str(d) for d in completed_deps[:BRIEFING_LIST_CAP]
        ],
    }
    if pr_review is not None:
        handoff["pr_review"] = pr_review
    return handoff


def _extract_pr_review(notes_structured: Any) -> dict[str, Any] | None:
    """Pull the canonical ``pr_review`` slot out of ``notes_structured``.

    Returns ``None`` when there is no structured note, no ``pr_review`` key, or
    the slot isn't a dict — so the handoff omits the field entirely (no
    misleading empty slot) for a task with no prior gate verdict. Only the
    well-typed scalar/list fields the gate writes are forwarded; anything else
    degrades to a safe default so a malformed slot never leaks a non-JSON
    object into the briefing.
    """
    if not isinstance(notes_structured, dict):
        return None
    raw = notes_structured.get("pr_review")
    if not isinstance(raw, dict):
        return None
    verdict = _typed(raw.get("verdict"), str, None)
    summary = _typed(raw.get("summary"), str, None)
    issues = _typed(raw.get("issues"), list, [])
    head_sha = _typed(raw.get("head_sha"), str, None)
    if not (verdict or summary or issues or head_sha):
        return None
    surface: dict[str, Any] = {"issues": list(issues[:BRIEFING_LIST_CAP])}
    if verdict:
        surface["verdict"] = verdict
    if summary:
        surface["summary"] = summary
    if head_sha:
        surface["head_sha"] = head_sha
    return surface


def build_context_briefing(inputs: BriefingInputs) -> dict[str, Any]:
    """Compose the context_briefing dict; caps each list at BRIEFING_LIST_CAP items."""
    return {
        "unread_a2a": inputs.unread_a2a[:BRIEFING_LIST_CAP],
        "unread_mentions": inputs.unread_mentions[:BRIEFING_LIST_CAP],
        "pending_notifications": inputs.pending_notifications[:BRIEFING_LIST_CAP],
        "task_metadata_gaps": list(inputs.task_metadata_gaps),
        "recent_team_activity": inputs.recent_team_activity[:BRIEFING_LIST_CAP],
        "blockers_in_my_lane": inputs.blockers_in_my_lane[:BRIEFING_LIST_CAP],
        "task_handoff": inputs.task_handoff,
        "company_goals": inputs.company_goals,
    }


def shape_memory_query(role: str, title: str, task_type: str) -> str:
    """Role-shape the institutional-memory query so each role retrieves what it
    actually needs: implementation lessons for a dev, decomposition lessons for a
    PM, defect patterns for QA, doc patterns for a documenter."""
    if role == "developer":
        return f"implementation lessons and playbooks for {title} ({task_type})"
    if role in ("cell_pm", "main_pm"):
        return f"decomposition and planning lessons for {title}"
    if role == "qa":
        return f"recurring defects and review feedback for {task_type}"
    if role == "documenter":
        return f"documentation patterns for {task_type}"
    return f"lessons and playbooks for {title}"

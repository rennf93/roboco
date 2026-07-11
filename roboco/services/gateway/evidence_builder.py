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

# EvidencePayload fields that are noise when empty — dropped from as_dict()
# rather than serialized as an empty list, matching build_task_handoff's
# key-absent-when-empty posture (an absent section reads as "nothing here"
# to the model, same as an empty one, at zero token cost).
_EVIDENCE_OMIT_WHEN_EMPTY = (
    "convention_findings",
    "revision_findings",
    "prior_findings",
)


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
    # The task's OPEN revision-ledger findings (qa_fail / pr_fail /
    # request_changes / ceo_reject) — rendered compactly, newest round
    # first, capped. Empty for a task with no open findings (zero noise).
    revision_findings: list[dict[str, Any]] = field(default_factory=list)
    # claim_review / claim_gate_review only: the FULL ledger (every status),
    # newest round first, so the round-N+1 reviewer verifies prior rounds
    # item-by-item instead of seeing only what is still open.
    prior_findings: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in _EVIDENCE_OMIT_WHEN_EMPTY:
            if not data[key]:
                del data[key]
        return data


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


# Char cap for a unified diff embedded in an LLM-facing payload (~5K tokens).
# The head is kept (file headers + earliest hunks carry the most signal); the
# marker points at the full diff so a reviewer is never silently blinded.
EVIDENCE_DIFF_CAP_CHARS = 20_000


def truncate_diff(diff: str | None, limit: int = EVIDENCE_DIFF_CAP_CHARS) -> str | None:
    """Cap a diff for envelope embedding; annotate what was omitted."""
    if not diff or len(diff) <= limit:
        return diff
    omitted = len(diff) - limit
    return (
        diff[:limit]
        + f"\n… [diff truncated: {omitted} chars omitted — read the full diff on"
        " the PR, or scope roboco_git_diff to a single file_path]"
    )


# Char cap for a single finding's ``evidence`` excerpt embedded in a
# briefing/evidence payload — the ledger row itself can carry up to 2000
# chars (Finding.evidence's own cap); this shrinks it for inline display.
FINDING_EVIDENCE_EXCERPT_CAP = 300


def _clip_with_note(text: str | None, cap: int) -> str | None:
    """Cap a short free-text field; annotate the omission (never silent).

    Sibling of ``truncate_diff``, sized for a single finding's excerpt
    rather than a full diff.
    """
    if not text or len(text) <= cap:
        return text
    omitted = len(text) - cap
    return text[:cap] + f"… [{omitted} chars omitted]"


def _render_finding(row: Any) -> dict[str, Any]:
    """One ledger row (``TaskReviewFindingTable``, duck-typed) -> a compact
    dict for a briefing/evidence payload."""
    return {
        "id": str(row.id)[:8],
        "round": row.round,
        "origin": row.origin,
        "status": row.status,
        "severity": row.severity,
        "file": row.file,
        "line": row.line,
        "expected": row.expected,
        "actual": row.actual,
        "fix": row.fix,
        "evidence": _clip_with_note(row.evidence, FINDING_EVIDENCE_EXCERPT_CAP),
    }


def render_findings(
    rows: list[Any] | None, *, cap: int = BRIEFING_LIST_CAP
) -> list[dict[str, Any]]:
    """Render ledger rows for a briefing/evidence payload, capped defensively
    (the caller's own DB query is expected to already cap; this is a second,
    cheap guard against a caller that doesn't)."""
    return [_render_finding(r) for r in (rows or [])[:cap]]


def build_evidence_for_task(
    task: Any,
    *,
    journal_highlights: list[dict[str, Any]],
    files_changed: list[str],
    pr_diff_summary: str | None = None,
    convention_findings: list[dict[str, Any]] | None = None,
    revision_findings: list[Any] | None = None,
    prior_findings: list[Any] | None = None,
) -> EvidencePayload:
    """Compose an EvidencePayload from a Task model + supplemental data.

    ``revision_findings`` / ``prior_findings`` take raw ledger rows (the
    caller fetches; this module stays DB-free) and render them via
    ``render_findings``.
    """
    return EvidencePayload(
        pr_number=task.pr_number,
        pr_url=task.pr_url,
        pr_diff_summary=truncate_diff(pr_diff_summary),
        commits=list(task.commits or []),
        files_changed=list(files_changed),
        dev_summary=task.dev_notes,
        journal_highlights=list(journal_highlights),
        acceptance_criteria_status=list(task.acceptance_criteria_status or []),
        convention_findings=list(convention_findings or []),
        revision_findings=render_findings(revision_findings),
        prior_findings=render_findings(prior_findings),
    )


def _typed(value: Any, expected: type | tuple[type, ...], default: Any) -> Any:
    """Return ``value`` only when it is the expected type, else ``default``.

    Keeps every handoff field serialisable: a bare mock or unexpected attribute
    type degrades to a safe default rather than leaking a non-JSON object.
    """
    return value if isinstance(value, expected) else default


def _has_prior_work(
    commits: list,
    acceptance: list,
    highlights: list,
    pr_number: int | None,
    dev_summary: str | None,
    completed_deps: list,
    pr_review: dict[str, Any] | None,
    qa_review: dict[str, Any] | None,
    pm_review: dict[str, Any] | None,
    open_findings: list,
) -> bool:
    """True when any resumable prior-work signal is present on the task."""
    return bool(
        commits
        or acceptance
        or highlights
        or pr_number is not None
        or dev_summary
        or completed_deps
        or pr_review is not None
        or qa_review is not None
        or pm_review is not None
        or open_findings
    )


def build_task_handoff(
    task: Any,
    journal_highlights: list[dict[str, Any]],
    open_findings: list[Any] | None = None,
) -> dict[str, Any] | None:
    """Compose a compact prior-work digest for the briefed task.

    Returns ``None`` when there is no task or no prior work worth resuming
    from, so the briefing only carries a handoff when one genuinely exists.
    DB-only by design — no git diff — so it is cheap enough to attach to
    every task-scoped briefing. ``open_findings`` takes raw ledger rows (the
    caller fetches — this module stays DB-free); rendered under
    ``revision_findings`` when non-empty.
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
    notes_structured = getattr(task, "notes_structured", None)
    # The persisted in-path PR-review gate verdict + concrete issues.
    # ``pr_fail`` writes ``notes_structured.pr_review``; surfacing it here puts
    # the concrete issues in every PM briefing so a respawned PM doesn't
    # re-submit the same PR blind.
    pr_review = _extract_pr_review(notes_structured)
    # The QA / PM-reject snapshots, parity with pr_review — full findings ride
    # ``revision_findings`` below, so these carry only verdict/summary/count.
    qa_review = _extract_qa_review(notes_structured)
    pm_review = _extract_pm_review(notes_structured)
    open_findings = list(open_findings or [])
    if not _has_prior_work(
        commits,
        acceptance,
        highlights,
        pr_number,
        dev_summary,
        completed_deps,
        pr_review,
        qa_review,
        pm_review,
        open_findings,
    ):
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
    if qa_review is not None:
        handoff["qa_review"] = qa_review
    if pm_review is not None:
        handoff["pm_review"] = pm_review
    if open_findings:
        handoff["revision_findings"] = render_findings(open_findings)
    return handoff


def _review_slot(notes_structured: Any, key: str) -> dict[str, Any] | None:
    """The raw ``notes_structured[key]`` dict, or ``None`` when absent /
    not a dict — shared by the ``pr_review`` / ``qa`` / ``pm_review``
    extractors below."""
    if not isinstance(notes_structured, dict):
        return None
    raw = notes_structured.get(key)
    return raw if isinstance(raw, dict) else None


def _extract_pr_review(notes_structured: Any) -> dict[str, Any] | None:
    """Pull the canonical ``pr_review`` slot out of ``notes_structured``.

    Returns ``None`` when there is no structured note, no ``pr_review`` key, or
    the slot isn't a dict — so the handoff omits the field entirely (no
    misleading empty slot) for a task with no prior gate verdict. Only the
    well-typed scalar/list fields the gate writes are forwarded; anything else
    degrades to a safe default so a malformed slot never leaks a non-JSON
    object into the briefing.
    """
    raw = _review_slot(notes_structured, "pr_review")
    if raw is None:
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


def _extract_qa_review(notes_structured: Any) -> dict[str, Any] | None:
    """Pull the ``qa`` slot out of ``notes_structured`` — parity with
    ``_extract_pr_review`` so a QA-bounced handoff surfaces the same way a
    gate-bounced one does. Full findings ride ``revision_findings``; this
    carries only verdict/summary/count so the QA snapshot isn't duplicated.
    """
    raw = _review_slot(notes_structured, "qa")
    if raw is None:
        return None
    verdict = _typed(raw.get("verdict"), str, None)
    summary = _typed(raw.get("summary"), str, None)
    findings_count = len(_typed(raw.get("findings"), list, []))
    if not (verdict or summary or findings_count):
        return None
    surface: dict[str, Any] = {"findings_count": findings_count}
    if verdict:
        surface["verdict"] = verdict
    if summary:
        surface["summary"] = summary
    return surface


def _extract_pm_review(notes_structured: Any) -> dict[str, Any] | None:
    """Pull the ``pm_review`` slot out of ``notes_structured``
    (``request_changes``'s ``PmReviewContent``) — no verdict field, since the
    transition to ``needs_revision`` IS the verdict (see that model's
    docstring)."""
    raw = _review_slot(notes_structured, "pm_review")
    if raw is None:
        return None
    summary = _typed(raw.get("summary"), str, None)
    findings_count = len(_typed(raw.get("findings"), list, []))
    if not (summary or findings_count):
        return None
    surface: dict[str, Any] = {"findings_count": findings_count}
    if summary:
        surface["summary"] = summary
    return surface


def build_context_briefing(inputs: BriefingInputs) -> dict[str, Any]:
    """Compose the context_briefing dict; caps each list at BRIEFING_LIST_CAP items.

    Empty sections (empty lists / dicts) are omitted: the agent reads this payload
    on every verb response, and an absent section reads as "nothing here" to the
    model, identical to an empty one, without the per-call token cost.
    """
    briefing: dict[str, Any] = {
        "unread_a2a": inputs.unread_a2a[:BRIEFING_LIST_CAP],
        "unread_mentions": inputs.unread_mentions[:BRIEFING_LIST_CAP],
        "pending_notifications": inputs.pending_notifications[:BRIEFING_LIST_CAP],
        "task_metadata_gaps": list(inputs.task_metadata_gaps),
        "recent_team_activity": inputs.recent_team_activity[:BRIEFING_LIST_CAP],
        "blockers_in_my_lane": inputs.blockers_in_my_lane[:BRIEFING_LIST_CAP],
        "task_handoff": inputs.task_handoff,
        "company_goals": inputs.company_goals,
    }
    return {key: value for key, value in briefing.items() if value}


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

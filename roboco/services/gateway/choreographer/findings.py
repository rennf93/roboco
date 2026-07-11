"""Shared producer-side helpers for the revision-findings ledger.

``fail_review`` (qa.py), ``pr_fail`` (pr_gate.py), and ``request_changes``
(_impl.py) all follow the same shape: normalize a legacy ``issues`` free-text
list into structured findings, cap the count, validate against the task's
acceptance criteria, persist to the ledger, and render the deterministic
per-finding text used for both the structured note's ``summary`` and the
A2A body. Centralized here so the three producers stay a thin, consistent
call instead of three drifting copies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from roboco.foundation.policy.content import Finding, Severity
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import BRIEFING_LIST_CAP
from roboco.services.repositories.review_findings import (
    STATUS_ADDRESSED,
    STATUS_OPEN,
    ReviewFindingsRepository,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

logger = structlog.get_logger()

# Two-tier count guardrail (mirrors ``_delegate_sizing_guard``'s shape): a
# soft nudge above 5 findings surfaces in the success envelope; a hard reject
# above 10 refuses the call outright — an oversized findings list is as
# unreviewable as an oversized task.
FINDINGS_NUDGE_COUNT = 5
FINDINGS_HARD_CAP = 10

_ISSUE_SHIM_EXPECTED = "task meets its acceptance criteria"


def next_round(task: Any) -> int:
    """The ledger round a finding written during THIS call belongs to.

    Read BEFORE the transition: ``TaskService._emit_status_transition_audit``
    bumps ``revision_count`` only on ENTRY into needs_revision, and the
    ledger insert happens in the same pre-transition step as the structured
    note write (mirrors ``_record_gate_verdict_for`` / ``_store_qa_note`` —
    both write before ``run_intent`` so the note and the transition commit
    together). So ``round`` is computed as ``current + 1``: the round this
    call is about to produce, not a value re-read after commit.
    """
    return (getattr(task, "revision_count", None) or 0) + 1


def issues_to_findings(issues: list[str]) -> list[dict[str, Any]]:
    """Shim: each free-text issue string -> a severity=major, file-less finding.

    Deprecated for one release (per spec) — ``findings=[...]`` is the
    structured replacement. Logs a deprecation warning so the migration off
    ``issues`` stays observable.
    """
    cleaned = [i for i in issues if i and i.strip()]
    if cleaned:
        logger.warning(
            "issues= is deprecated on this verb; pass "
            "findings=[{file, severity, expected, actual}, ...] instead",
            count=len(cleaned),
        )
    return [
        {
            "severity": Severity.MAJOR.value,
            "expected": _ISSUE_SHIM_EXPECTED,
            "actual": issue.strip(),
        }
        for issue in cleaned
    ]


def merge_findings_and_issues(
    findings: list[dict[str, Any]] | None, issues: list[str] | None
) -> list[dict[str, Any]]:
    """Combine structured ``findings`` with the legacy ``issues`` shim.

    The three producers (fail_review / pr_fail / request_changes) used to
    pick ONE source (``findings if findings else issues_to_findings(issues)``)
    — a caller sending both had its ``issues`` silently dropped. Merging keeps
    every concern the caller raised, subject to the same combined count
    guardrail as either alone.
    """
    merged = list(findings or [])
    if issues:
        merged.extend(issues_to_findings(issues))
    return merged


def findings_count_guard(findings: Sequence[Any]) -> Envelope | None:
    """Hard-reject a findings list over ``FINDINGS_HARD_CAP``, else None."""
    if len(findings) <= FINDINGS_HARD_CAP:
        return None
    return Envelope.invalid_state(
        message=(
            f"{len(findings)} findings in one call — too many independent "
            f"concerns for one revision round (cap {FINDINGS_HARD_CAP})."
        ),
        remediate=(
            "split across multiple calls, or prioritize the blocking ones "
            "first — an oversized findings list is as unreviewable as an "
            "oversized task."
        ),
    )


def findings_count_hint(findings: Sequence[Any]) -> str | None:
    """Soft nudge above ``FINDINGS_NUDGE_COUNT``, surfaced in the success
    envelope (never blocking)."""
    if len(findings) <= FINDINGS_NUDGE_COUNT:
        return None
    return (
        f"{len(findings)} findings in one call. If they span unrelated "
        "concerns, a tighter, higher-signal set next round is easier for the "
        "developer to act on. (Allowed, just flagged.)"
    )


def unknown_finding_criteria(task: Any, findings: list[Finding]) -> list[str]:
    """Findings whose ``criterion`` matches neither an AC id nor AC text.

    Mirrors ``TaskService.unknown_ac_refs`` — a criterion may be supplied by
    its stable id (``acceptance_criteria_ids``, migration 036) or its exact
    text, since both representations already circulate (``declare_coverage``
    accepts either). Findings with no ``criterion`` are unconstrained.

    Short-circuits to ``[]`` (never touching ``task.acceptance_criteria*``)
    when no finding supplies a criterion — the common case — so a task
    stub/mock missing those attributes never breaks a criterion-less call.
    """
    criteria = [f.criterion for f in findings if f.criterion]
    if not criteria:
        return []
    valid_ids = set(getattr(task, "acceptance_criteria_ids", None) or [])
    valid_texts = set(getattr(task, "acceptance_criteria", None) or [])
    return [c for c in criteria if c not in valid_ids and c not in valid_texts]


def criterion_mismatch_rejection(task: Any, unknown: list[str]) -> Envelope:
    """Rejection envelope for one or more unresolvable finding criteria."""
    valid = list(getattr(task, "acceptance_criteria_ids", None) or [])
    return Envelope.invalid_state(
        message=(
            f"finding criterion not found on this task: {unknown!r}. Valid "
            f"criterion ids: {valid!r}"
        ),
        remediate=(
            "each finding's `criterion` must match one of the task's "
            "acceptance criteria (by id or exact text) — omit `criterion` "
            "if the finding is not tied to a specific one"
        ),
    )


def render_finding_line(finding_id: UUID | None, finding: Finding) -> str:
    """One deterministic line:
    '[F-<id8>] file:line (severity) — expected → actual → fix'.
    """
    prefix = f"[F-{str(finding_id)[:8]}] " if finding_id is not None else ""
    loc = finding.file or "—"
    if finding.line is not None:
        loc += f":{finding.line}"
    fix = f" → {finding.fix}" if finding.fix else ""
    body = f"{finding.expected} → {finding.actual}{fix}"
    return f"{prefix}{loc} ({finding.severity.value}) — {body}"


def render_findings_summary(rows: list[tuple[UUID | None, Finding]]) -> str:
    """The deterministic per-finding rendering — one line per finding.

    Used as both the structured note's ``summary`` field and the A2A body
    for a findings-driven fail/reject, so the bounced dev sees the exact
    same text everywhere it lands.
    """
    return "\n".join(render_finding_line(fid, f) for fid, f in rows)


async def insert_and_render(
    session: Any,
    *,
    task_id: UUID,
    origin: str,
    round: int,
    author_slug: str | None,
    findings: list[Finding],
) -> tuple[list[Any], str]:
    """Insert one ledger row per finding, then render the id-prefixed summary.

    Returns ``(rows, summary)`` — ``rows`` for callers that need the ledger
    ids (e.g. to feed evidence), ``summary`` for the note/A2A text.
    """
    repo = ReviewFindingsRepository(session)
    rows = await repo.insert_many(
        task_id=task_id,
        origin=origin,
        round=round,
        author_slug=author_slug,
        findings=findings,
    )
    summary = render_findings_summary(
        list(zip((row.id for row in rows), findings, strict=True))
    )
    return rows, summary


async def open_findings_for_task(
    session: Any, task_id: UUID, *, limit: int = BRIEFING_LIST_CAP
) -> list[Any]:
    """Open ledger rows for a task, newest round first, capped — for a
    briefing/evidence payload. Fails open (empty list) on any lookup error,
    same posture as every other best-effort DB read on this path."""
    try:
        repo = ReviewFindingsRepository(session)
        return await repo.list_for_task(task_id, status=STATUS_OPEN, limit=limit)
    except Exception:
        logger.warning("open findings fetch failed", task_id=str(task_id))
        return []


async def full_ledger_for_task(
    session: Any, task_id: UUID, *, limit: int = BRIEFING_LIST_CAP
) -> list[Any]:
    """The task's full findings ledger (every status), newest round first,
    capped — for claim_review / claim_gate_review, so a round-N+1 reviewer
    verifies prior rounds item-by-item. Fails open (empty list) on any
    lookup error."""
    try:
        repo = ReviewFindingsRepository(session)
        return await repo.list_for_task(task_id, limit=limit)
    except Exception:
        logger.warning("full ledger fetch failed", task_id=str(task_id))
        return []


async def stamp_addressed_verified(session: Any, task_id: UUID, *, origin: str) -> int:
    """Bulk-verify every ADDRESSED finding of ``origin`` on this task — QA
    passing / the in-path gate passing IS the confirmation.

    Runs in the caller's own session with no independent commit (mirrors
    ``insert_and_render``'s same-transaction posture) — a repo error
    propagates to the caller, so a stamping failure fails the enclosing verb
    cleanly instead of landing a passed/gated task against a stale ledger.
    Returns the count of rows verified (0 when none qualify).
    """
    repo = ReviewFindingsRepository(session)
    addressed = await repo.list_for_task(task_id, status=STATUS_ADDRESSED)
    ids = [row.id for row in addressed if row.origin == origin]
    return await repo.mark_verified(ids)

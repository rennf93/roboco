"""The in-path gate persists its verdict to ``notes_structured.pr_review``.

Without this, ``pr_pass`` / ``pr_fail`` only threaded their notes through the
tracing-gate shim and posted to GitHub — they never wrote the task's structured
PR-reviewer slot. So a task that was passed once and later failed kept showing
the stale ``verdict: passed`` while the real transition was ``pr_fail`` →
needs_revision (observed live on root fead4372 / PR #107). The gate now authors
the canonical ``pr_review`` note on every decision so the slot can never
contradict the transition.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from roboco.foundation.policy.content import Finding, Severity
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_choreographer() -> Choreographer:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    return Choreographer(ChoreographerDeps(**base))


class _Task:
    def __init__(self) -> None:
        self.id = uuid4()
        # A stale PASS verdict from an earlier pr_pass.
        self.notes_structured: dict[str, Any] | None = {
            "pr_review": {
                "summary": "All seven acceptance criteria are satisfied.",
                "findings": [],
                "verdict": "passed",
            }
        }
        self.pr_reviewer_notes = "stale"


class _TaskWithNoNotes:
    """Variant where notes_structured starts as None (no prior verdict history)."""

    def __init__(self) -> None:
        self.id = uuid4()
        self.notes_structured: dict[str, Any] | None = None
        self.pr_reviewer_notes: str = ""


def test_pr_fail_overwrites_stale_passed_verdict() -> None:
    c = _make_choreographer()
    t = _Task()
    c._record_gate_verdict(
        t, "pr_fail", "Issues:\n- docs contradict AC1\n- README states wrong count"
    )
    assert t.notes_structured is not None
    assert t.notes_structured["pr_review"]["verdict"] == "failed"
    assert "docs contradict AC1" in t.notes_structured["pr_review"]["summary"]
    # The derived TEXT mirror is regenerated too.
    assert "failed" in t.pr_reviewer_notes.lower()


def test_pr_pass_records_passed_verdict() -> None:
    c = _make_choreographer()
    # Use the None-initial variant so mypy sees the broader declared type
    # (dict[str, Any] | None) rather than a narrowed None from an in-body
    # assignment, which would make the post-call assertions look unreachable.
    t = _TaskWithNoNotes()
    c._record_gate_verdict(
        t, "pr_pass", "Assembled root scope is clean; every criterion is covered."
    )
    assert t.notes_structured is not None
    assert t.notes_structured["pr_review"]["verdict"] == "passed"


def test_record_gate_verdict_swallows_invalid_note() -> None:
    """A too-short summary would fail content validation — it must never raise
    (the transition already committed); the slot is just left untouched."""
    c = _make_choreographer()
    t = _Task()
    c._record_gate_verdict(t, "pr_fail", "x")  # below the summary minimum
    # No exception, and the stale slot is left as-is rather than corrupted.
    assert t.notes_structured is not None
    assert t.notes_structured["pr_review"]["verdict"] == "passed"


def test_pr_fail_stores_issues_structurally_not_summary_only() -> None:
    """pr_fail's free-text issues must persist into the structured ``issues``
    slot (so a reader of notes_structured.pr_review gets the concrete
    change-requests), not be flattened into the summary string alone."""
    c = _make_choreographer()
    t = _TaskWithNoNotes()
    c._record_gate_verdict(
        t,
        "pr_fail",
        "Issues:\n- seam mismatch\n- docs lag the diff",
        issues=("seam mismatch", "docs lag the diff"),
    )
    assert t.notes_structured is not None
    slot = t.notes_structured["pr_review"]
    assert slot["verdict"] == "failed"
    assert slot["issues"] == ["seam mismatch", "docs lag the diff"]
    # The derived TEXT mirror surfaces them too, so pr_reviewer_notes carries
    # the concrete change-requests for any future reader.
    assert "seam mismatch" in t.pr_reviewer_notes
    assert "docs lag the diff" in t.pr_reviewer_notes


def test_pr_fail_summary_does_not_duplicate_issues() -> None:
    """pr_fail's issues must render only under ## Issues, not also baked into
    ## Summary — otherwise the Task Details "PR Reviewer Notes" card shows each
    issue twice (once under Summary, once under Issues). The summary is a
    substantive non-issues sentence; the structured ``issues`` slot carries the
    change-requests. ``notes`` (with the issues) still drives the GitHub PR post
    and the a2a to the owning PM — those are raw text, not rendered through
    ``render_markdown``, so no duplication there."""
    c = _make_choreographer()
    t = _TaskWithNoNotes()
    c._record_gate_verdict(
        t,
        "pr_fail",
        "Issues:\n- seam mismatch\n- docs lag the diff",
        issues=("seam mismatch", "docs lag the diff"),
    )
    assert t.notes_structured is not None
    slot = t.notes_structured["pr_review"]
    assert slot["verdict"] == "failed"
    # Issues live in the structured issues slot...
    assert slot["issues"] == ["seam mismatch", "docs lag the diff"]
    # ...NOT baked into the summary.
    assert "seam mismatch" not in slot["summary"]
    assert "docs lag the diff" not in slot["summary"]
    # The rendered TEXT mirror surfaces each issue (under ## Issues)...
    assert "seam mismatch" in t.pr_reviewer_notes
    assert "docs lag the diff" in t.pr_reviewer_notes
    # ...with both section headers present, and the summary is the substantive
    # sentence (not the issues-joined string).
    assert "## Summary" in t.pr_reviewer_notes
    assert "## Issues" in t.pr_reviewer_notes
    assert "requested changes" in t.pr_reviewer_notes


def test_pr_pass_leaves_issues_slot_empty() -> None:
    c = _make_choreographer()
    t = _TaskWithNoNotes()
    c._record_gate_verdict(
        t, "pr_pass", "Assembled root scope is clean; every criterion is covered."
    )
    assert t.notes_structured is not None
    slot = t.notes_structured["pr_review"]
    assert slot["verdict"] == "passed"
    assert slot.get("issues", []) == []


def test_pr_fail_embeds_findings_and_summary_does_not_duplicate() -> None:
    """The revision-findings ledger's structured findings must land in the
    format-enforced ``findings`` slot (its own render_markdown table already
    displays them); ``summary`` stays the plain "N issue(s)" sentence — baking
    the per-finding text into both would duplicate every line on the Task
    Details card (the same anti-duplication the free-text ``issues`` case
    already established)."""
    c = _make_choreographer()
    t = _TaskWithNoNotes()
    findings = [
        Finding(
            file="roboco/api/routes/health.py",
            line=12,
            severity=Severity.MAJOR,
            expected="returns 200",
            actual="returns 500 on the timestamp branch",
        )
    ]
    c._record_gate_verdict(
        t,
        "pr_fail",
        "[F-abc12345] roboco/api/routes/health.py:12 (major) — returns 200 → "
        "returns 500 on the timestamp branch",
        findings=findings,
    )
    assert t.notes_structured is not None
    slot = t.notes_structured["pr_review"]
    assert slot["verdict"] == "failed"
    assert len(slot["findings"]) == 1
    assert slot["findings"][0]["actual"] == "returns 500 on the timestamp branch"
    assert "1 issue(s) listed below" in slot["summary"]
    assert "returns 500 on the timestamp branch" not in slot["summary"]
    # The derived TEXT mirror renders the findings table (render_markdown).
    assert "returns 500 on the timestamp branch" in t.pr_reviewer_notes

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

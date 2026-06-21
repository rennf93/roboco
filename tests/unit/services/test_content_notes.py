"""Tests for the apply_structured_note chokepoint."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from roboco.foundation.policy.content import ContentValidationError, PrReviewContent
from roboco.services.content_notes import apply_structured_note


def _task() -> SimpleNamespace:
    return SimpleNamespace(
        notes_structured=None,
        dev_notes=None,
        qa_notes=None,
        auditor_notes=None,
        doc_notes=None,
        pr_reviewer_notes=None,
        quick_context=None,
    )


def test_pr_review_lands_in_own_slot_not_qa() -> None:
    t = _task()
    t.qa_notes = "QA already wrote this"
    model = apply_structured_note(
        t,
        "pr_review",
        {
            "summary": "Guard missing on the 422 path here.",
            "verdict": "changes_requested",
            "findings": [
                {
                    "file": "git.py",
                    "severity": "blocker",
                    "expected": "retry as COMMENT",
                    "actual": "raises",
                }
            ],
        },
    )
    assert isinstance(model, PrReviewContent)
    assert t.notes_structured["pr_review"]["verdict"] == "changes_requested"
    assert t.pr_reviewer_notes == model.render_markdown()
    # QA's slot is untouched.
    assert t.qa_notes == "QA already wrote this"


def test_qa_mirror_regenerated() -> None:
    t = _task()
    apply_structured_note(
        t,
        "qa",
        {
            "summary": "Verified every acceptance criterion.",
            "ac_verdicts": [
                {"criterion": "AC1", "status": "verified", "how": "test passes"}
            ],
            "verdict": "passed",
        },
    )
    assert t.notes_structured["qa"]["verdict"] == "passed"
    assert "## Acceptance Criteria" in t.qa_notes


def test_resumption_writes_quick_context() -> None:
    t = _task()
    apply_structured_note(
        t, "resumption", {"done": "schema landed", "next": "wire the gateway"}
    )
    assert "## Done" in t.quick_context
    assert t.notes_structured["resumption"]["next"] == "wire the gateway"


def test_doc_writes_doc_notes() -> None:
    t = _task()
    apply_structured_note(t, "doc", {"summary": "Documented the new endpoints."})
    assert "## Summary" in t.doc_notes


def test_invalid_payload_leaves_task_untouched() -> None:
    t = _task()
    with pytest.raises(ContentValidationError):
        apply_structured_note(t, "pr_review", {"verdict": "approved"})  # no summary
    assert t.notes_structured is None
    assert t.pr_reviewer_notes is None


def test_notes_structured_reassigned_for_dirty_tracking() -> None:
    t = _task()
    t.notes_structured = {"developer": {"summary": "x"}}
    before = t.notes_structured
    apply_structured_note(t, "doc", {"summary": "Documented the endpoints fully."})
    assert t.notes_structured is not before  # new dict object
    assert "developer" in t.notes_structured  # prior entry preserved
    assert "doc" in t.notes_structured

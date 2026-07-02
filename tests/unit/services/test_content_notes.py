"""Tests for the apply_structured_note chokepoint."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from roboco.foundation.policy.content import ContentValidationError, PrReviewContent
from roboco.services.content_notes import apply_structured_note, content_type_for_role


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


def test_content_type_for_role_maps_section_roles() -> None:
    """Each role with a dedicated section routes to its content type."""
    assert content_type_for_role("developer") == "developer"
    assert content_type_for_role("qa") == "qa"
    assert content_type_for_role("documenter") == "doc"
    assert content_type_for_role("pr_reviewer") == "pr_review"
    assert content_type_for_role("auditor") == "auditor"
    assert content_type_for_role("cell_pm") == "resumption"
    assert content_type_for_role("main_pm") == "resumption"


def test_content_type_for_role_none_for_sectionless_roles() -> None:
    """Board / advisory / on-demand roles have no dedicated section."""
    assert content_type_for_role("product_owner") is None
    assert content_type_for_role("head_marketing") is None
    assert content_type_for_role("ceo") is None
    assert content_type_for_role("prompter") is None


def test_sections_carry_written_at_stamp() -> None:
    """Every persisted section carries an ISO written_at — traces without
    timestamps were unusable for reconstructing WHEN a note landed (CEO
    reMarkable item, 2026-07-02)."""
    from datetime import datetime

    t = _task()
    apply_structured_note(
        t,
        "developer",
        {
            "summary": (
                "Built the greeting module end to end; single additive file "
                "on the task branch with the PR open against the base."
            )
        },
    )
    stored = (t.notes_structured or {})["developer"]
    assert "written_at" in stored, stored
    # Parseable, timezone-aware ISO-8601.
    parsed = datetime.fromisoformat(stored["written_at"])
    assert parsed.tzinfo is not None


def test_written_at_refreshes_on_rewrite() -> None:
    t = _task()
    payload = {
        "summary": (
            "First pass of the notes section, long enough to validate "
            "against the dev section's minimum content length."
        )
    }
    apply_structured_note(t, "developer", payload)
    first = (t.notes_structured or {})["developer"]["written_at"]
    apply_structured_note(t, "developer", payload)
    second = (t.notes_structured or {})["developer"]["written_at"]
    assert second >= first

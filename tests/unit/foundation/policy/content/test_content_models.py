"""Validation tests for the structured content schema models."""

from __future__ import annotations

import pytest
from roboco.foundation.policy.content import (
    AuditorNote,
    ContentValidationError,
    DeveloperNote,
    DocNote,
    PrReviewContent,
    QaNote,
    ResumptionNote,
    TaskDescription,
    validate_content,
)
from roboco.foundation.policy.content.enums import Severity, Verdict

# --------------------------------------------------------------------------- #
# Valid construction
# --------------------------------------------------------------------------- #


def test_pr_review_valid() -> None:
    c = PrReviewContent.model_validate(
        {
            "summary": "The change is correct and covered by tests.",
            "findings": [
                {
                    "file": "roboco/services/git.py",
                    "line": 42,
                    "severity": "major",
                    "expected": "raises on 422",
                    "actual": "swallows the error",
                }
            ],
            "verdict": "changes_requested",
        }
    )
    assert c.findings[0].severity is Severity.MAJOR
    assert c.verdict is Verdict.CHANGES_REQUESTED


def test_qa_valid() -> None:
    c = QaNote.model_validate(
        {
            "summary": "Reviewed all acceptance criteria against the diff.",
            "ac_verdicts": [
                {
                    "criterion": "AC1 returns 400",
                    "status": "verified",
                    "how": "test passes",
                }
            ],
            "verdict": "passed",
        }
    )
    assert c.ac_verdicts[0].status == "verified"


def test_task_description_valid() -> None:
    c = TaskDescription.model_validate(
        {
            "objective": "Add a structured PR-review comment format.",
            "what_this_builds": ["a reviewer schema"],
            "the_work": [
                {
                    "team": "backend",
                    "summary": "schema + gateway",
                    "items": ["model", "verb"],
                }
            ],
            "acceptance_criteria": ["reviewer notes land in their own slot"],
        }
    )
    assert c.the_work[0].team.value == "backend"


def test_resumption_valid() -> None:
    c = ResumptionNote(
        done="schema landed", next="wire the gateway", where_to_look=["models.py"]
    )
    assert c.next == "wire the gateway"


# --------------------------------------------------------------------------- #
# Rejection
# --------------------------------------------------------------------------- #


def test_validate_content_unknown_type() -> None:
    with pytest.raises(ContentValidationError) as exc:
        validate_content("nonsense", {})
    assert exc.value.field == "content_type"


def test_pr_review_missing_summary_rejected() -> None:
    with pytest.raises(ContentValidationError) as exc:
        validate_content("pr_review", {"verdict": "approved"})
    assert exc.value.field == "summary"


def test_pr_review_trivial_summary_rejected() -> None:
    with pytest.raises(ContentValidationError):
        validate_content("pr_review", {"summary": "wip", "verdict": "approved"})


def test_pr_review_negative_verdict_allows_summary_only() -> None:
    # A reviewer can fail on a summary alone (e.g. "CI is red"); findings are
    # format-enforced (file/line/expected/actual) when present, not mandatory.
    c = validate_content(
        "pr_review",
        {
            "summary": "CI is red on this PR; the failing job blocks merge.",
            "verdict": "failed",
            "findings": [],
        },
    )
    assert isinstance(c, PrReviewContent)
    assert c.verdict.value == "failed"


def test_pr_review_approved_allows_empty_findings() -> None:
    c = validate_content(
        "pr_review",
        {"summary": "All criteria met, nothing to change.", "verdict": "approved"},
    )
    assert isinstance(c, PrReviewContent)


def test_qa_verdict_must_be_pass_or_fail() -> None:
    with pytest.raises(ContentValidationError) as exc:
        validate_content(
            "qa",
            {
                "summary": "Reviewed the acceptance criteria thoroughly.",
                "ac_verdicts": [
                    {"criterion": "c", "status": "verified", "how": "test"}
                ],
                "verdict": "approved",
            },
        )
    assert exc.value.field == "verdict"


def test_qa_allows_empty_ac_verdicts() -> None:
    # ac_verdicts is optional (a QA fail can be summary-only); the verb's
    # coverage gate enforces per-criterion verdicts for a pass.
    c = validate_content(
        "qa",
        {
            "summary": "Reviewed everything carefully here.",
            "ac_verdicts": [],
            "verdict": "failed",
        },
    )
    assert isinstance(c, QaNote)
    assert c.ac_verdicts == []


def test_task_description_requires_work() -> None:
    with pytest.raises(ContentValidationError) as exc:
        validate_content(
            "task_description",
            {
                "objective": "Build the thing properly.",
                "the_work": [],
                "acceptance_criteria": ["x"],
            },
        )
    assert exc.value.field == "the_work"


def test_work_unit_rejects_non_cell_team() -> None:
    with pytest.raises(ContentValidationError):
        validate_content(
            "task_description",
            {
                "objective": "Build the thing properly.",
                "the_work": [{"team": "board", "summary": "do it", "items": ["a"]}],
                "acceptance_criteria": ["x"],
            },
        )


# --------------------------------------------------------------------------- #
# Graceful coercion (lone scalar -> one-element list)
# --------------------------------------------------------------------------- #


def test_findings_single_dict_coerced_to_list() -> None:
    c = validate_content(
        "pr_review",
        {
            "summary": "One finding passed as a bare dict.",
            "verdict": "changes_requested",
            "findings": {
                "file": "a.py",
                "severity": "nit",
                "expected": "trailing newline",
                "actual": "no newline",
            },
        },
    )
    assert isinstance(c, PrReviewContent)
    assert len(c.findings) == 1


def test_pr_review_issues_carry_free_text_change_requests() -> None:
    # The in-path gate fails on free-text issues (not structured Finding
    # objects, which require file/severity/expected/actual). Those issues now
    # land in the additive `issues` slot instead of being flattened into the
    # summary string alone — so a reader of notes_structured.pr_review gets the
    # concrete change-requests, and the rendered TEXT mirror gains an Issues
    # section.
    c = validate_content(
        "pr_review",
        {
            "summary": "PR review needs changes before this can merge.",
            "verdict": "changes_requested",
            "issues": ["seam mismatch on the rebase path", "docs lag the diff"],
        },
    )
    assert isinstance(c, PrReviewContent)
    assert c.issues == ["seam mismatch on the rebase path", "docs lag the diff"]
    rendered = c.render_markdown()
    assert "## Issues" in rendered
    assert "seam mismatch on the rebase path" in rendered
    assert "docs lag the diff" in rendered


def test_pr_review_issues_default_empty_and_single_scalar_coerced() -> None:
    c = validate_content(
        "pr_review",
        {"summary": "Clean PR, no free-text issues to raise.", "verdict": "approved"},
    )
    assert isinstance(c, PrReviewContent)
    assert c.issues == []
    assert "## Issues" not in c.render_markdown()

    coerced = validate_content(
        "pr_review",
        {
            "summary": "One free-text issue passed as a bare string here.",
            "verdict": "changes_requested",
            "issues": "lone issue string",
        },
    )
    assert coerced.issues == ["lone issue string"]


def test_where_to_look_single_string_coerced() -> None:
    c = validate_content(
        "resumption",
        {
            "done": "did the thing",
            "next": "do next thing",
            "where_to_look": "models.py",
        },
    )
    assert isinstance(c, ResumptionNote)
    assert c.where_to_look == ["models.py"]


def test_developer_and_doc_and_auditor_models() -> None:
    assert isinstance(
        validate_content("developer", {"summary": "Implemented the schema layer."}),
        DeveloperNote,
    )
    assert isinstance(
        validate_content("doc", {"summary": "Documented the new endpoints."}), DocNote
    )
    assert isinstance(
        validate_content(
            "auditor", {"summary": "No concerns with this change.", "severity": "info"}
        ),
        AuditorNote,
    )

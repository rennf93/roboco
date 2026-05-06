"""Tests for tracing-completeness preconditions."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from roboco.services.gateway.tracing_gate import (
    GateContext,
    Requirement,
    check_requirements,
)


def _task() -> MagicMock:
    """Bare Task fixture; tests set the attributes they need."""
    t = MagicMock()
    t.id = uuid4()
    t.plan = None
    t.progress_updates = []
    t.acceptance_criteria = []
    t.acceptance_criteria_status = []
    t.qa_notes = None
    t.qa_evidence_inspected = False
    t.self_verified = False
    return t


class TestCheckRequirements:
    def test_plan_present(self) -> None:
        t = _task()
        t.plan = {"steps": ["a", "b"]}
        result = check_requirements(t, [Requirement.PLAN])
        assert result.passed is True

    def test_plan_missing(self) -> None:
        t = _task()
        result = check_requirements(t, [Requirement.PLAN])
        assert result.passed is False
        assert "plan" in result.missing[0].lower()

    def test_progress_present(self) -> None:
        t = _task()
        t.progress_updates = [{"message": "did stuff", "ts": "..."}]
        result = check_requirements(t, [Requirement.PROGRESS_AT_LEAST_ONE])
        assert result.passed is True

    def test_progress_missing(self) -> None:
        t = _task()
        result = check_requirements(t, [Requirement.PROGRESS_AT_LEAST_ONE])
        assert result.passed is False

    def test_journal_reflect_required(self) -> None:
        t = _task()
        absent = check_requirements(
            t, [Requirement.JOURNAL_REFLECT], GateContext(journal_reflect_present=False)
        )
        assert absent.passed is False
        present = check_requirements(
            t, [Requirement.JOURNAL_REFLECT], GateContext(journal_reflect_present=True)
        )
        assert present.passed is True

    def test_acceptance_criteria_all_addressed(self) -> None:
        t = _task()
        t.acceptance_criteria = ["AC1", "AC2"]
        t.acceptance_criteria_status = [
            {"criterion": "AC1", "referencing_artifact_id": "commit-abc"},
            {"criterion": "AC2", "referencing_artifact_id": "note-xyz"},
        ]
        result = check_requirements(t, [Requirement.ACCEPTANCE_CRITERIA_ADDRESSED])
        assert result.passed is True

    def test_acceptance_criteria_partial_fails(self) -> None:
        t = _task()
        t.acceptance_criteria = ["AC1", "AC2", "AC3"]
        t.acceptance_criteria_status = [
            {"criterion": "AC1", "referencing_artifact_id": "commit-abc"},
        ]
        result = check_requirements(t, [Requirement.ACCEPTANCE_CRITERIA_ADDRESSED])
        assert result.passed is False
        assert any("AC2" in m or "AC3" in m for m in result.missing)

    def test_qa_notes_min_chars(self) -> None:
        t = _task()
        t.qa_notes = "short"
        result = check_requirements(
            t, [Requirement.QA_NOTES_MIN_CHARS], GateContext(qa_notes_min_chars=80)
        )
        assert result.passed is False

    def test_qa_evidence_inspected(self) -> None:
        t = _task()
        result = check_requirements(t, [Requirement.QA_EVIDENCE_INSPECTED])
        assert result.passed is False

    def test_combined_pass(self) -> None:
        t = _task()
        t.plan = {"steps": ["x"]}
        t.progress_updates = [{"message": "did"}]
        t.acceptance_criteria = ["AC1"]
        t.acceptance_criteria_status = [
            {"criterion": "AC1", "referencing_artifact_id": "c-1"}
        ]
        result = check_requirements(
            t,
            [
                Requirement.PLAN,
                Requirement.PROGRESS_AT_LEAST_ONE,
                Requirement.ACCEPTANCE_CRITERIA_ADDRESSED,
                Requirement.JOURNAL_REFLECT,
            ],
            GateContext(journal_reflect_present=True),
        )
        assert result.passed is True

    def test_journal_decision_present(self) -> None:
        """Line 64: journal:decision present passes."""
        t = _task()
        result = check_requirements(
            t,
            [Requirement.JOURNAL_DECISION],
            GateContext(journal_decision_present=True),
        )
        assert result.passed is True

    def test_journal_decision_missing(self) -> None:
        t = _task()
        result = check_requirements(
            t,
            [Requirement.JOURNAL_DECISION],
            GateContext(journal_decision_present=False),
        )
        assert result.passed is False
        assert "journal:decision" in result.missing

    def test_journal_learning_present(self) -> None:
        """Line 68: journal:learning present passes."""
        t = _task()
        result = check_requirements(
            t,
            [Requirement.JOURNAL_LEARNING],
            GateContext(journal_learning_present=True),
        )
        assert result.passed is True

    def test_journal_learning_missing(self) -> None:
        t = _task()
        result = check_requirements(
            t,
            [Requirement.JOURNAL_LEARNING],
            GateContext(journal_learning_present=False),
        )
        assert result.passed is False
        assert "journal:learning" in result.missing

    def test_self_verified_present(self) -> None:
        """Line 85: self_verified=True passes."""
        t = _task()
        t.self_verified = True
        result = check_requirements(t, [Requirement.SELF_VERIFIED])
        assert result.passed is True

    def test_self_verified_missing(self) -> None:
        t = _task()
        t.self_verified = False
        result = check_requirements(t, [Requirement.SELF_VERIFIED])
        assert result.passed is False
        assert "self_verified" in result.missing

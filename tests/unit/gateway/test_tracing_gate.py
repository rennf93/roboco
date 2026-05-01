"""Tests for tracing-completeness preconditions."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from roboco.services.gateway.tracing_gate import (
    GateResult,
    Requirement,
    check_requirements,
)


def _task(*, plan=None, progress_updates=None, acceptance_criteria=None,
          acceptance_criteria_status=None, qa_notes=None, qa_evidence_inspected=False,
          self_verified=False):
    t = MagicMock()
    t.id = uuid4()
    t.plan = plan
    t.progress_updates = progress_updates or []
    t.acceptance_criteria = acceptance_criteria or []
    t.acceptance_criteria_status = acceptance_criteria_status or []
    t.qa_notes = qa_notes
    t.qa_evidence_inspected = qa_evidence_inspected
    t.self_verified = self_verified
    return t


class TestCheckRequirements:
    def test_plan_present(self) -> None:
        t = _task(plan={"steps": ["a", "b"]})
        result = check_requirements(t, [Requirement.PLAN], journal_reflect_present=False)
        assert result.passed is True

    def test_plan_missing(self) -> None:
        t = _task(plan=None)
        result = check_requirements(t, [Requirement.PLAN], journal_reflect_present=False)
        assert result.passed is False
        assert "plan" in result.missing[0].lower()

    def test_progress_present(self) -> None:
        t = _task(progress_updates=[{"message": "did stuff", "ts": "..."}])
        result = check_requirements(
            t, [Requirement.PROGRESS_AT_LEAST_ONE], journal_reflect_present=False
        )
        assert result.passed is True

    def test_progress_missing(self) -> None:
        t = _task(progress_updates=[])
        result = check_requirements(
            t, [Requirement.PROGRESS_AT_LEAST_ONE], journal_reflect_present=False
        )
        assert result.passed is False

    def test_journal_reflect_required(self) -> None:
        t = _task()
        result = check_requirements(
            t, [Requirement.JOURNAL_REFLECT], journal_reflect_present=False
        )
        assert result.passed is False
        result_ok = check_requirements(
            t, [Requirement.JOURNAL_REFLECT], journal_reflect_present=True
        )
        assert result_ok.passed is True

    def test_acceptance_criteria_all_addressed(self) -> None:
        t = _task(
            acceptance_criteria=["AC1", "AC2"],
            acceptance_criteria_status=[
                {"criterion": "AC1", "referencing_artifact_id": "commit-abc"},
                {"criterion": "AC2", "referencing_artifact_id": "note-xyz"},
            ],
        )
        result = check_requirements(
            t,
            [Requirement.ACCEPTANCE_CRITERIA_ADDRESSED],
            journal_reflect_present=False,
        )
        assert result.passed is True

    def test_acceptance_criteria_partial_fails(self) -> None:
        t = _task(
            acceptance_criteria=["AC1", "AC2", "AC3"],
            acceptance_criteria_status=[
                {"criterion": "AC1", "referencing_artifact_id": "commit-abc"},
            ],
        )
        result = check_requirements(
            t,
            [Requirement.ACCEPTANCE_CRITERIA_ADDRESSED],
            journal_reflect_present=False,
        )
        assert result.passed is False
        assert any("AC2" in m or "AC3" in m for m in result.missing)

    def test_qa_notes_min_chars(self) -> None:
        t = _task(qa_notes="short")
        result = check_requirements(
            t,
            [Requirement.QA_NOTES_MIN_CHARS],
            journal_reflect_present=False,
            qa_notes_min_chars=80,
        )
        assert result.passed is False

    def test_qa_evidence_inspected(self) -> None:
        t = _task(qa_evidence_inspected=False)
        result = check_requirements(
            t,
            [Requirement.QA_EVIDENCE_INSPECTED],
            journal_reflect_present=False,
        )
        assert result.passed is False

    def test_combined_pass(self) -> None:
        t = _task(
            plan={"steps": ["x"]},
            progress_updates=[{"message": "did"}],
            acceptance_criteria=["AC1"],
            acceptance_criteria_status=[
                {"criterion": "AC1", "referencing_artifact_id": "c-1"}
            ],
        )
        result = check_requirements(
            t,
            [
                Requirement.PLAN,
                Requirement.PROGRESS_AT_LEAST_ONE,
                Requirement.ACCEPTANCE_CRITERIA_ADDRESSED,
                Requirement.JOURNAL_REFLECT,
            ],
            journal_reflect_present=True,
        )
        assert result.passed is True

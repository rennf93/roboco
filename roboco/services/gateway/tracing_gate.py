"""Precondition checks for tracing completeness.

Pure functions over a Task model + ambient context (`journal_reflect_present`,
`qa_notes_min_chars`). The choreographer queries journal/qa state and passes
booleans/scalars in; this module decides pass/fail and returns the missing
requirements with concrete error keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Requirement(StrEnum):
    PLAN = "plan"
    PROGRESS_AT_LEAST_ONE = "progress>=1"
    JOURNAL_REFLECT = "journal:reflect"
    JOURNAL_DECISION = "journal:decision"
    JOURNAL_LEARNING = "journal:learning"
    ACCEPTANCE_CRITERIA_ADDRESSED = "acceptance_criteria_addressed"
    QA_NOTES_MIN_CHARS = "qa_notes>=min"
    QA_EVIDENCE_INSPECTED = "qa_evidence_inspected"
    SELF_VERIFIED = "self_verified"


@dataclass(frozen=True)
class GateResult:
    passed: bool
    missing: list[str]


def check_requirements(
    task: Any,
    requirements: list[Requirement],
    *,
    journal_reflect_present: bool = False,
    journal_decision_present: bool = False,
    journal_learning_present: bool = False,
    qa_notes_min_chars: int = 80,
) -> GateResult:
    """Check that every requirement is met. Returns pass + list of missing keys."""
    missing: list[str] = []
    for req in requirements:
        if req is Requirement.PLAN:
            if not task.plan:
                missing.append("plan")
        elif req is Requirement.PROGRESS_AT_LEAST_ONE:
            if not task.progress_updates or len(task.progress_updates) < 1:
                missing.append("progress>=1")
        elif req is Requirement.JOURNAL_REFLECT:
            if not journal_reflect_present:
                missing.append("journal:reflect")
        elif req is Requirement.JOURNAL_DECISION:
            if not journal_decision_present:
                missing.append("journal:decision")
        elif req is Requirement.JOURNAL_LEARNING:
            if not journal_learning_present:
                missing.append("journal:learning")
        elif req is Requirement.ACCEPTANCE_CRITERIA_ADDRESSED:
            unaddressed = _unaddressed_criteria(task)
            if unaddressed:
                for c in unaddressed:
                    missing.append(f"acceptance_criterion:{c}")
        elif req is Requirement.QA_NOTES_MIN_CHARS:
            if not task.qa_notes or len(task.qa_notes) < qa_notes_min_chars:
                missing.append("qa_notes>=min")
        elif req is Requirement.QA_EVIDENCE_INSPECTED:
            if not task.qa_evidence_inspected:
                missing.append("qa_evidence_inspected")
        elif req is Requirement.SELF_VERIFIED:
            if not task.self_verified:
                missing.append("self_verified")
    return GateResult(passed=len(missing) == 0, missing=missing)


def _unaddressed_criteria(task: Any) -> list[str]:
    """Return acceptance criteria text values that have no referencing artifact."""
    criteria: list[str] = list(task.acceptance_criteria or [])
    status: list[dict] = list(task.acceptance_criteria_status or [])
    addressed = {
        s["criterion"]
        for s in status
        if isinstance(s, dict) and s.get("referencing_artifact_id")
    }
    return [c for c in criteria if c not in addressed]

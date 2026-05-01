"""Precondition checks for tracing completeness.

Pure functions over a Task model + a GateContext (ambient flags + thresholds).
The choreographer queries journal/qa state, builds a GateContext, and calls
check_requirements; this module decides pass/fail and returns the missing
requirements with concrete error keys.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
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
class GateContext:
    """Ambient inputs the checker needs that don't live on the Task model."""

    journal_reflect_present: bool = False
    journal_decision_present: bool = False
    journal_learning_present: bool = False
    qa_notes_min_chars: int = 80


@dataclass(frozen=True)
class GateResult:
    passed: bool
    missing: list[str] = field(default_factory=list)


# A Checker takes (task, ctx) and returns the missing key(s) — empty list when
# the requirement is satisfied.
Checker = Callable[[Any, GateContext], list[str]]


def _check_plan(task: Any, _ctx: GateContext) -> list[str]:
    return [] if task.plan else ["plan"]


def _check_progress(task: Any, _ctx: GateContext) -> list[str]:
    has_progress = bool(task.progress_updates) and len(task.progress_updates) >= 1
    return [] if has_progress else ["progress>=1"]


def _check_journal_reflect(_task: Any, ctx: GateContext) -> list[str]:
    return [] if ctx.journal_reflect_present else ["journal:reflect"]


def _check_journal_decision(_task: Any, ctx: GateContext) -> list[str]:
    return [] if ctx.journal_decision_present else ["journal:decision"]


def _check_journal_learning(_task: Any, ctx: GateContext) -> list[str]:
    return [] if ctx.journal_learning_present else ["journal:learning"]


def _check_acceptance_criteria(task: Any, _ctx: GateContext) -> list[str]:
    return [f"acceptance_criterion:{c}" for c in _unaddressed_criteria(task)]


def _check_qa_notes_min_chars(task: Any, ctx: GateContext) -> list[str]:
    notes = task.qa_notes or ""
    return [] if len(notes) >= ctx.qa_notes_min_chars else ["qa_notes>=min"]


def _check_qa_evidence_inspected(task: Any, _ctx: GateContext) -> list[str]:
    return [] if task.qa_evidence_inspected else ["qa_evidence_inspected"]


def _check_self_verified(task: Any, _ctx: GateContext) -> list[str]:
    return [] if task.self_verified else ["self_verified"]


_CHECKERS: dict[Requirement, Checker] = {
    Requirement.PLAN: _check_plan,
    Requirement.PROGRESS_AT_LEAST_ONE: _check_progress,
    Requirement.JOURNAL_REFLECT: _check_journal_reflect,
    Requirement.JOURNAL_DECISION: _check_journal_decision,
    Requirement.JOURNAL_LEARNING: _check_journal_learning,
    Requirement.ACCEPTANCE_CRITERIA_ADDRESSED: _check_acceptance_criteria,
    Requirement.QA_NOTES_MIN_CHARS: _check_qa_notes_min_chars,
    Requirement.QA_EVIDENCE_INSPECTED: _check_qa_evidence_inspected,
    Requirement.SELF_VERIFIED: _check_self_verified,
}


def check_requirements(
    task: Any,
    requirements: list[Requirement],
    ctx: GateContext | None = None,
) -> GateResult:
    """Check that every requirement is met. Returns pass + list of missing keys."""
    context = ctx or GateContext()
    missing: list[str] = []
    for req in requirements:
        missing.extend(_CHECKERS[req](task, context))
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

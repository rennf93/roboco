"""Tracing-gate policy — verb→required-set table + check_requirements.

Replaces:
  - services/gateway/tracing_gate.py (the entire module)
  - 6 inline `journal:decision` checks scattered in choreographer/_impl.py
  - inline gates in choreographer/qa.py (QA pass/fail)
  - inline gates in choreographer/doc.py (i_documented)

Adds (per spec §11 P1-P4 pre-gateway parity restorations):
  - JOURNAL_NOTE_AT_CLAIM       — required by i_will_work_on
  - JOURNAL_DECISION_AT_CLAIM   — required by i_will_plan
  - JOURNAL_DURING_WORK_AT_LEAST_ONE — required by i_am_done
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Requirement(StrEnum):
    PLAN = "plan"
    COMMITS_AT_LEAST_ONE = "commits>=1"
    PR_OPEN = "pr_open"
    PROGRESS_AT_LEAST_ONE = "progress>=1"
    JOURNAL_REFLECT = "journal:reflect"
    JOURNAL_DECISION = "journal:decision"
    JOURNAL_LEARNING = "journal:learning"
    JOURNAL_STRUGGLE = "journal:struggle"
    JOURNAL_NOTE_AT_CLAIM = "journal:note_at_claim"
    JOURNAL_DECISION_AT_CLAIM = "journal:decision_at_claim"
    JOURNAL_DURING_WORK_AT_LEAST_ONE = "journal:during_work>=1"
    ACCEPTANCE_CRITERIA_ADDRESSED = "acceptance_criteria_addressed"
    QA_NOTES_MIN_CHARS = "qa_notes>=min"
    QA_EVIDENCE_INSPECTED = "qa_evidence_inspected"
    DOCS_NOTES_MIN_CHARS = "docs_notes>=min"
    DOCS_FILES_NON_EMPTY = "docs_files_non_empty"
    SELF_VERIFIED = "self_verified"
    NOTES_MIN_CHARS = "notes>=min"
    SUBTASKS_TERMINAL = "subtasks_terminal"


@dataclass(frozen=True)
class GateContext:
    """Ambient inputs the checker needs that don't live on the Task model."""

    journal_reflect_present: bool = False
    journal_decision_present: bool = False
    journal_learning_present: bool = False
    journal_struggle_present: bool = False
    journal_note_at_claim_present: bool = False
    journal_during_work_count: int = 0
    qa_notes_min_chars: int = 80
    docs_notes_min_chars: int = 20
    notes_min_chars: int = 20


@dataclass(frozen=True)
class GateResult:
    passed: bool
    missing: list[str] = field(default_factory=list)


Checker = Callable[[Any, GateContext], list[str]]


def _check_plan(task: Any, _ctx: GateContext) -> list[str]:
    return [] if getattr(task, "plan", None) else ["plan"]


def _check_commits(task: Any, _ctx: GateContext) -> list[str]:
    commits = getattr(task, "commits", None) or []
    return [] if len(commits) >= 1 else ["commits>=1"]


def _check_pr_open(task: Any, _ctx: GateContext) -> list[str]:
    return [] if getattr(task, "pr_number", None) else ["pr_open"]


def _check_progress(task: Any, _ctx: GateContext) -> list[str]:
    progress = getattr(task, "progress_updates", None) or []
    return [] if len(progress) >= 1 else ["progress>=1"]


def _check_journal_reflect(_task: Any, ctx: GateContext) -> list[str]:
    return [] if ctx.journal_reflect_present else ["journal:reflect"]


def _check_journal_decision(_task: Any, ctx: GateContext) -> list[str]:
    return [] if ctx.journal_decision_present else ["journal:decision"]


def _check_journal_learning(_task: Any, ctx: GateContext) -> list[str]:
    return [] if ctx.journal_learning_present else ["journal:learning"]


def _check_journal_struggle(_task: Any, ctx: GateContext) -> list[str]:
    return [] if ctx.journal_struggle_present else ["journal:struggle"]


def _check_journal_note_at_claim(_task: Any, ctx: GateContext) -> list[str]:
    return [] if ctx.journal_note_at_claim_present else ["journal:note_at_claim"]


def _check_journal_decision_at_claim(_task: Any, ctx: GateContext) -> list[str]:
    # Reuse JOURNAL_DECISION presence flag; "_at_claim" timing is the
    # caller's responsibility (i_will_plan only requires a decision entry
    # exists for this task by this agent — its position in the timeline
    # is enforced by the verb's call order, not the gate).
    return [] if ctx.journal_decision_present else ["journal:decision_at_claim"]


def _check_journal_during_work(_task: Any, ctx: GateContext) -> list[str]:
    return [] if ctx.journal_during_work_count >= 1 else ["journal:during_work>=1"]


def _unaddressed_criteria(task: Any) -> list[str]:
    criteria = list(getattr(task, "acceptance_criteria", []) or [])
    status_rows = list(getattr(task, "acceptance_criteria_status", []) or [])
    addressed = {
        s["criterion"]
        for s in status_rows
        if isinstance(s, dict) and s.get("referencing_artifact_id")
    }
    return [c for c in criteria if c not in addressed]


def _check_acceptance_criteria(task: Any, ctx: GateContext) -> list[str]:
    """Reflect-note serves as the addressing artifact when explicit
    per-criterion citation is absent. See spec §9 item 1."""
    if ctx.journal_reflect_present:
        return []
    return [f"acceptance_criterion:{c}" for c in _unaddressed_criteria(task)]


def _check_qa_notes_min_chars(task: Any, ctx: GateContext) -> list[str]:
    notes = getattr(task, "qa_notes", "") or ""
    return [] if len(notes) >= ctx.qa_notes_min_chars else ["qa_notes>=min"]


def _check_qa_evidence_inspected(task: Any, _ctx: GateContext) -> list[str]:
    return (
        []
        if getattr(task, "qa_evidence_inspected", False)
        else ["qa_evidence_inspected"]
    )


def _check_docs_notes_min_chars(task: Any, ctx: GateContext) -> list[str]:
    notes = getattr(task, "dev_notes", "") or ""
    return [] if len(notes) >= ctx.docs_notes_min_chars else ["docs_notes>=min"]


def _check_docs_files_non_empty(task: Any, _ctx: GateContext) -> list[str]:
    docs = getattr(task, "documents", None) or []
    return [] if len(docs) >= 1 else ["docs_files_non_empty"]


def _check_self_verified(task: Any, _ctx: GateContext) -> list[str]:
    return [] if getattr(task, "self_verified", False) else ["self_verified"]


def _check_notes_min_chars(task: Any, ctx: GateContext) -> list[str]:
    notes = getattr(task, "notes", "") or ""
    return [] if len(notes) >= ctx.notes_min_chars else ["notes>=min"]


def _check_subtasks_terminal(task: Any, _ctx: GateContext) -> list[str]:
    """Caller passes a task whose `_subtasks_all_terminal` boolean is set
    by the choreographer based on a DB query. Validator just reads it."""
    return (
        [] if getattr(task, "_subtasks_all_terminal", False) else ["subtasks_terminal"]
    )


_CHECKERS: dict[Requirement, Checker] = {
    Requirement.PLAN: _check_plan,
    Requirement.COMMITS_AT_LEAST_ONE: _check_commits,
    Requirement.PR_OPEN: _check_pr_open,
    Requirement.PROGRESS_AT_LEAST_ONE: _check_progress,
    Requirement.JOURNAL_REFLECT: _check_journal_reflect,
    Requirement.JOURNAL_DECISION: _check_journal_decision,
    Requirement.JOURNAL_LEARNING: _check_journal_learning,
    Requirement.JOURNAL_STRUGGLE: _check_journal_struggle,
    Requirement.JOURNAL_NOTE_AT_CLAIM: _check_journal_note_at_claim,
    Requirement.JOURNAL_DECISION_AT_CLAIM: _check_journal_decision_at_claim,
    Requirement.JOURNAL_DURING_WORK_AT_LEAST_ONE: _check_journal_during_work,
    Requirement.ACCEPTANCE_CRITERIA_ADDRESSED: _check_acceptance_criteria,
    Requirement.QA_NOTES_MIN_CHARS: _check_qa_notes_min_chars,
    Requirement.QA_EVIDENCE_INSPECTED: _check_qa_evidence_inspected,
    Requirement.DOCS_NOTES_MIN_CHARS: _check_docs_notes_min_chars,
    Requirement.DOCS_FILES_NON_EMPTY: _check_docs_files_non_empty,
    Requirement.SELF_VERIFIED: _check_self_verified,
    Requirement.NOTES_MIN_CHARS: _check_notes_min_chars,
    Requirement.SUBTASKS_TERMINAL: _check_subtasks_terminal,
}


def check_requirements(
    *,
    task: Any,
    requirements: list[Requirement],
    ctx: GateContext | None = None,
) -> GateResult:
    """Run every requirement in `requirements` against `task` + `ctx`.

    Returns GateResult(passed, missing) — `missing` is empty on pass.
    """
    context = ctx or GateContext()
    missing: list[str] = []
    for req in requirements:
        missing.extend(_CHECKERS[req](task, context))
    return GateResult(passed=len(missing) == 0, missing=missing)


# Verb name → required Requirements (single source of truth).
VERB_REQUIREMENTS: dict[str, frozenset[Requirement]] = {
    # Developer claim — pre-gateway DEVELOPER.md required a work_log entry on claim.
    # PLAN mirrors spec.PRECONDITION_PLAN at the tracing layer (single source of truth).
    "i_will_work_on": frozenset(
        {
            Requirement.PLAN,
            Requirement.JOURNAL_NOTE_AT_CLAIM,
        }
    ),
    # PM claim — pre-gateway PM.md required a journal:decision on plan.
    "i_will_plan": frozenset(
        {
            Requirement.PLAN,
            Requirement.JOURNAL_DECISION_AT_CLAIM,
        }
    ),
    # PM delegate — pre-gateway PM.md required journal:decision before each delegate.
    "delegate": frozenset({Requirement.JOURNAL_DECISION}),
    # Developer submit — adds JOURNAL_DURING_WORK_AT_LEAST_ONE for mid-flight cadence.
    # SELF_VERIFIED is set by the auto-run in_progress→verifying transition; it
    # stays in the required-set as a defense-in-depth backstop.
    "i_am_done": frozenset(
        {
            Requirement.COMMITS_AT_LEAST_ONE,
            Requirement.PR_OPEN,
            Requirement.PROGRESS_AT_LEAST_ONE,
            Requirement.SELF_VERIFIED,
            Requirement.JOURNAL_REFLECT,
            Requirement.JOURNAL_DURING_WORK_AT_LEAST_ONE,
            Requirement.ACCEPTANCE_CRITERIA_ADDRESSED,
        }
    ),
    # QA pass/fail.
    "pass_review": frozenset(
        {
            Requirement.QA_NOTES_MIN_CHARS,
            Requirement.QA_EVIDENCE_INSPECTED,
            Requirement.JOURNAL_LEARNING,
        }
    ),
    "fail_review": frozenset(
        {
            Requirement.QA_NOTES_MIN_CHARS,
            Requirement.QA_EVIDENCE_INSPECTED,
            Requirement.JOURNAL_LEARNING,
        }
    ),
    # Doc submit.
    "i_documented": frozenset(
        {
            Requirement.DOCS_FILES_NON_EMPTY,
            Requirement.DOCS_NOTES_MIN_CHARS,
            Requirement.JOURNAL_REFLECT,
        }
    ),
    # PM submit-up — adds JOURNAL_REFLECT (pre-gateway required decision AND reflect).
    "submit_up": frozenset(
        {
            Requirement.SUBTASKS_TERMINAL,
            Requirement.JOURNAL_DECISION,
            Requirement.JOURNAL_REFLECT,
            Requirement.NOTES_MIN_CHARS,
        }
    ),
    # PM complete — adds JOURNAL_REFLECT (parity with submit_up).
    "complete": frozenset(
        {
            Requirement.JOURNAL_DECISION,
            Requirement.JOURNAL_REFLECT,
            Requirement.NOTES_MIN_CHARS,
        }
    ),
    # PM unblock — was inline at _impl.py:2192-2200; now declared.
    "unblock": frozenset({Requirement.JOURNAL_DECISION}),
    # PM escalate up — was inline.
    "escalate_up": frozenset({Requirement.JOURNAL_DECISION}),
    # Board/MainPM escalate to CEO.
    "escalate_to_ceo": frozenset({Requirement.JOURNAL_DECISION}),
    # Developer block — pre-gateway required journal:struggle.
    "i_am_blocked": frozenset({Requirement.JOURNAL_STRUGGLE}),
}


# Verbs that intentionally have no tracing requirement (read-only / discovery /
# pure state moves). Each entry is a deliberate decision, not an oversight.
VERBS_WITHOUT_TRACING: frozenset[str] = frozenset(
    {
        "give_me_work",  # discovery — no state change
        "triage",  # read-only listing
        "triage_all",  # read-only listing
        "evidence",  # read-only evidence dump
        "i_am_idle",  # signal only
        "unclaim",  # voluntary release; no rationale required
        "reassign",  # mechanical intra-cell hand-off; branch/WIP preserved
        "resume",  # pure state move paused→in_progress
        # claim_review's tracing applies on pass_review / fail_review.
        "claim_review",
        # claim_doc_task's tracing applies on i_documented.
        "claim_doc_task",
        # open_pr is a mechanical push+open; preconditions are inline.
        "open_pr",
    }
)


def requirements_for(verb: str) -> frozenset[Requirement]:
    """Lookup the required-set for a verb.

    Raises KeyError when the verb is neither in VERB_REQUIREMENTS nor in
    VERBS_WITHOUT_TRACING — caller should never reach a verb name unknown to
    foundation.
    """
    if verb in VERB_REQUIREMENTS:
        return VERB_REQUIREMENTS[verb]
    if verb in VERBS_WITHOUT_TRACING:
        return frozenset()
    raise KeyError(
        f"unknown verb in tracing table: {verb!r} "
        f"(known: {sorted(set(VERB_REQUIREMENTS) | VERBS_WITHOUT_TRACING)})"
    )

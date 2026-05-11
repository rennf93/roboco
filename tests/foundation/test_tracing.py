"""Tier 1 — tracing Requirement enum + check_requirements scaffolding."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from roboco.foundation.policy import tracing


def test_requirement_enum_has_canonical_values() -> None:
    """Required-set vocabulary mirrors the pre-Phase-2 tracing_gate.py
    PLUS the new pre-gateway parity additions."""
    expected = {
        "plan",
        "commits>=1",
        "pr_open",
        "progress>=1",
        "journal:reflect",
        "journal:decision",
        "journal:learning",
        "journal:struggle",
        "journal:note_at_claim",
        "journal:decision_at_claim",
        "journal:during_work>=1",
        "acceptance_criteria_addressed",
        "qa_notes>=min",
        "qa_evidence_inspected",
        "docs_notes>=min",
        "docs_files_non_empty",
        "self_verified",
        "notes>=min",
        "subtasks_terminal",
    }
    actual = {r.value for r in tracing.Requirement}
    assert actual == expected, f"Requirement drift: {actual ^ expected}"


def test_gate_context_has_journal_presence_flags() -> None:
    ctx = tracing.GateContext()
    assert ctx.journal_reflect_present is False
    assert ctx.journal_decision_present is False
    assert ctx.journal_learning_present is False
    assert ctx.journal_struggle_present is False
    assert ctx.journal_note_at_claim_present is False
    assert ctx.journal_during_work_count == 0


def test_gate_result_has_passed_and_missing() -> None:
    result = tracing.GateResult(passed=True)
    assert result.passed is True
    assert result.missing == []


def test_check_requirements_passes_when_all_satisfied() -> None:
    task = SimpleNamespace(
        plan={"x": 1},
        commits=[{"sha": "abc"}],
        pr_number=42,
        progress_updates=[{"message": "x"}],
        acceptance_criteria=[],
        acceptance_criteria_status=[],
    )
    ctx = tracing.GateContext(journal_reflect_present=True)
    result = tracing.check_requirements(
        task=task,
        requirements=[
            tracing.Requirement.PLAN,
            tracing.Requirement.COMMITS_AT_LEAST_ONE,
            tracing.Requirement.PR_OPEN,
            tracing.Requirement.JOURNAL_REFLECT,
        ],
        ctx=ctx,
    )
    assert result.passed is True


def test_check_requirements_returns_missing_keys_on_failure() -> None:
    task = SimpleNamespace(
        plan=None,
        commits=[],
        pr_number=None,
        progress_updates=[],
        acceptance_criteria=[],
        acceptance_criteria_status=[],
    )
    result = tracing.check_requirements(
        task=task,
        requirements=[
            tracing.Requirement.PLAN,
            tracing.Requirement.COMMITS_AT_LEAST_ONE,
            tracing.Requirement.PR_OPEN,
            tracing.Requirement.JOURNAL_REFLECT,
        ],
    )
    assert result.passed is False
    assert "plan" in result.missing
    assert "commits>=1" in result.missing
    assert "pr_open" in result.missing
    assert "journal:reflect" in result.missing


def test_acceptance_criteria_check_treats_reflect_note_as_addressing_artifact() -> None:
    """Spec §9 item 1: reflect-note clears the criteria gate."""
    task = SimpleNamespace(
        acceptance_criteria=["AC1", "AC2"],
        acceptance_criteria_status=[],
    )
    ctx = tracing.GateContext(journal_reflect_present=True)
    result = tracing.check_requirements(
        task=task,
        requirements=[tracing.Requirement.ACCEPTANCE_CRITERIA_ADDRESSED],
        ctx=ctx,
    )
    assert result.passed is True


def test_during_work_count_satisfies_requirement() -> None:
    task = SimpleNamespace()
    ctx = tracing.GateContext(journal_during_work_count=1)
    result = tracing.check_requirements(
        task=task,
        requirements=[tracing.Requirement.JOURNAL_DURING_WORK_AT_LEAST_ONE],
        ctx=ctx,
    )
    assert result.passed is True


def test_verb_requirements_covers_pm_decision_chain() -> None:
    """The 6 inline journal:decision callsites' verbs all require it."""
    for verb in (
        "submit_up",
        "complete",
        "unblock",
        "escalate_up",
        "escalate_to_ceo",
        "delegate",
    ):
        reqs = tracing.requirements_for(verb)
        assert tracing.Requirement.JOURNAL_DECISION in reqs, (
            f"{verb} should require journal:decision per spec §11"
        )


def test_verb_requirements_covers_dev_completion_chain() -> None:
    reqs = tracing.requirements_for("i_am_done")
    assert tracing.Requirement.COMMITS_AT_LEAST_ONE in reqs
    assert tracing.Requirement.PR_OPEN in reqs
    assert tracing.Requirement.PROGRESS_AT_LEAST_ONE in reqs
    assert tracing.Requirement.JOURNAL_REFLECT in reqs
    assert tracing.Requirement.JOURNAL_DURING_WORK_AT_LEAST_ONE in reqs
    assert tracing.Requirement.ACCEPTANCE_CRITERIA_ADDRESSED in reqs


def test_verb_requirements_includes_pre_gateway_parity_at_claim() -> None:
    assert tracing.Requirement.JOURNAL_NOTE_AT_CLAIM in tracing.requirements_for(
        "i_will_work_on"
    )
    assert tracing.Requirement.JOURNAL_DECISION_AT_CLAIM in tracing.requirements_for(
        "i_will_plan"
    )


def test_pm_complete_requires_both_decision_and_reflect() -> None:
    """Pre-gateway parity P4: PMs wrote both before complete."""
    reqs = tracing.requirements_for("complete")
    assert tracing.Requirement.JOURNAL_DECISION in reqs
    assert tracing.Requirement.JOURNAL_REFLECT in reqs


def test_qa_pass_review_requires_learning() -> None:
    reqs = tracing.requirements_for("pass_review")
    assert tracing.Requirement.QA_NOTES_MIN_CHARS in reqs
    assert tracing.Requirement.QA_EVIDENCE_INSPECTED in reqs
    assert tracing.Requirement.JOURNAL_LEARNING in reqs


def test_i_am_blocked_requires_struggle_journal() -> None:
    """Lifts JOURNAL_STRUGGLE out of dangling-enum status."""
    assert tracing.Requirement.JOURNAL_STRUGGLE in tracing.requirements_for(
        "i_am_blocked"
    )


def test_requirements_for_unknown_verb_raises_key_error() -> None:
    with pytest.raises(KeyError):
        tracing.requirements_for("not_a_real_verb")

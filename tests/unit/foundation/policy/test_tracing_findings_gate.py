"""The FINDINGS_ADDRESSED tracing requirement — i_am_done's ledger resolution gate.

Pure unit tests against ``foundation.policy.tracing`` (no DB, no choreographer):
the checker itself, its registration in VERB_REQUIREMENTS["i_am_done"], and the
"empty ledger passes untouched" contract.
"""

from __future__ import annotations

from roboco.foundation.policy import tracing as tr


def test_findings_addressed_is_required_by_i_am_done() -> None:
    assert tr.Requirement.FINDINGS_ADDRESSED in tr.VERB_REQUIREMENTS["i_am_done"]


def test_no_open_findings_passes_trivially() -> None:
    result = tr.check_requirements(
        task=object(),
        requirements=[tr.Requirement.FINDINGS_ADDRESSED],
        ctx=tr.GateContext(open_finding_ids=()),
    )
    assert result.passed
    assert result.missing == []


def test_open_findings_block_and_name_each_id() -> None:
    result = tr.check_requirements(
        task=object(),
        requirements=[tr.Requirement.FINDINGS_ADDRESSED],
        ctx=tr.GateContext(open_finding_ids=("abc12345", "def67890")),
    )
    assert not result.passed
    assert result.missing == ["finding:abc12345", "finding:def67890"]


def test_default_gate_context_has_no_open_findings() -> None:
    assert tr.GateContext().open_finding_ids == ()


def test_i_am_done_requirements_include_the_pre_existing_set_too() -> None:
    """Adding FINDINGS_ADDRESSED must not have dropped any prior requirement."""
    required = tr.VERB_REQUIREMENTS["i_am_done"]
    for expected in (
        tr.Requirement.COMMITS_AT_LEAST_ONE,
        tr.Requirement.PR_OPEN,
        tr.Requirement.PROGRESS_AT_LEAST_ONE,
        tr.Requirement.SELF_VERIFIED,
        tr.Requirement.JOURNAL_REFLECT,
        tr.Requirement.JOURNAL_DURING_WORK_AT_LEAST_ONE,
        tr.Requirement.ACCEPTANCE_CRITERIA_ADDRESSED,
        tr.Requirement.DEV_NOTES_MIN_CHARS,
    ):
        assert expected in required

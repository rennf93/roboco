"""Tests for remediation hint generation."""

from __future__ import annotations

from roboco.db.tables import TaskTable
from roboco.services.gateway.remediation import (
    hint_for_missing_ac_coverage,
    hint_for_missing_progress,
    hint_for_missing_reflect,
    hint_for_unaddressed_acceptance_criteria,
)
from roboco.services.task import TaskService


def test_missing_progress_hint() -> None:
    h = hint_for_missing_progress()
    assert "commit" in h.lower() or "progress" in h.lower()


def test_missing_reflect_hint() -> None:
    h = hint_for_missing_reflect(task_id="xyz-789")
    assert "note(scope='reflect'" in h
    assert "xyz-789" in h


def test_unaddressed_criteria_hint() -> None:
    h = hint_for_unaddressed_acceptance_criteria(
        criteria=["criterion 1", "criterion 3"], task_id="t-1"
    )
    assert "criterion 1" in h
    assert "criterion 3" in h
    assert "t-1" in h


def test_missing_ac_coverage_hint_shows_call_shape_and_real_ids() -> None:
    h = hint_for_missing_ac_coverage(
        ids=["id-a", "id-b"],
        texts=["Criterion A", "Criterion B"],
        title="Orphan slice",
    )
    assert "delegate(title='Orphan slice'" in h
    assert "covers_parent_criteria=['id-a']" in h
    assert 'id-a="Criterion A"' in h
    assert 'id-b="Criterion B"' in h


def test_missing_ac_coverage_hint_names_both_legal_reference_forms() -> None:
    """The remediate names both legal ``covers_parent_criteria`` forms —
    a criterion's id or its exact text — not just id."""
    h = hint_for_missing_ac_coverage(ids=["id-a"], texts=["Criterion A"], title="X")
    assert "id(s) or exact text(s)" in h


def test_missing_ac_coverage_hint_handles_no_criteria() -> None:
    h = hint_for_missing_ac_coverage(ids=[], texts=[], title="X")
    assert "<id>" in h


def test_missing_ac_coverage_hint_empty_ids_uses_quoted_text() -> None:
    """A legacy/unhealed parent whose ids are empty must still get a real,
    copy-pasteable reference for every criterion — never a `'<id>'`
    placeholder, and never a truncated listing."""
    texts = ["Criterion A", "Criterion B", "Criterion C"]
    h = hint_for_missing_ac_coverage(ids=[], texts=texts, title="Orphan slice")

    assert "'<id>'" not in h
    for text in texts:
        assert text in h

    parent = TaskTable(acceptance_criteria_ids=[], acceptance_criteria=texts)
    for text in texts:
        assert TaskService.unknown_ac_refs(parent, [text]) == []


def test_missing_ac_coverage_hint_drifted_ids_shorter_than_criteria() -> None:
    """1 id against 3 criteria: every criterion is listed (id for the
    first, quoted text for the rest) — zip's `strict=False` used to drop
    the tail criteria silently."""
    texts = ["Criterion A", "Criterion B", "Criterion C"]
    h = hint_for_missing_ac_coverage(ids=["id-a"], texts=texts, title="Orphan slice")

    assert "'<id>'" not in h
    assert 'id-a="Criterion A"' in h
    for text in texts[1:]:
        assert text in h

    parent = TaskTable(acceptance_criteria_ids=["id-a"], acceptance_criteria=texts)
    assert TaskService.unknown_ac_refs(parent, ["id-a"]) == []
    for text in texts[1:]:
        assert TaskService.unknown_ac_refs(parent, [text]) == []

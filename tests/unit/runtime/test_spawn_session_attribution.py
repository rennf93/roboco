"""Spawn-session task_id attribution warning (#11).

A developer / QA / documenter spawn always works a concrete task — a spawn row
with ``task_id IS NULL`` is an unattributed-cost bug (the usage rollup can't tie
the spend to a task). Intake (prompter), secretary, auditor, and PMs legitimately
spawn taskless, so they are NOT flagged. The pure predicate lives next to the
spawn recorder so the warning fires only for the genuine signal.
"""

from __future__ import annotations

from roboco.runtime.orchestrator import is_unattributed_delivery_spawn


def test_developer_spawn_without_task_is_unattributed() -> None:
    assert is_unattributed_delivery_spawn("developer", None) is True


def test_qa_and_documenter_spawns_without_task_are_unattributed() -> None:
    assert is_unattributed_delivery_spawn("qa", None) is True
    assert is_unattributed_delivery_spawn("documenter", None) is True


def test_delivery_spawn_with_task_is_attributed() -> None:
    assert is_unattributed_delivery_spawn("developer", "abc-123") is False
    assert is_unattributed_delivery_spawn("qa", "abc-123") is False


def test_intake_secretary_auditor_spawns_without_task_are_not_suspect() -> None:
    # Human-only / observer roles legitimately have no task.
    assert is_unattributed_delivery_spawn("prompter", None) is False
    assert is_unattributed_delivery_spawn("secretary", None) is False
    assert is_unattributed_delivery_spawn("auditor", None) is False


def test_pm_spawns_without_task_are_not_suspect() -> None:
    # PMs coordinate and may legitimately spawn taskless to plan / triage.
    assert is_unattributed_delivery_spawn("main_pm", None) is False
    assert is_unattributed_delivery_spawn("cell_pm", None) is False


def test_role_comparison_is_case_insensitive() -> None:
    assert is_unattributed_delivery_spawn("Developer", None) is True

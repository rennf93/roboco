"""TaskService._audit_events_for — the rejector-attributed audit event selection.

A transition always emits the generic ``task.<status>``; a reviewer bounce to
needs_revision additionally emits ``task.qa_fail`` / ``task.pr_fail`` /
``task.request_changes`` / ``task.ceo_reject`` keyed on the acting role, so the
per-agent rework scorecard can attribute the rejection.
"""

from __future__ import annotations

from roboco.services.task import TaskService


def test_generic_transition_emits_only_status_event() -> None:
    assert TaskService._audit_events_for("awaiting_qa", "developer") == [
        "task.awaiting_qa"
    ]


def test_qa_fail_adds_named_event() -> None:
    assert TaskService._audit_events_for("needs_revision", "qa") == [
        "task.needs_revision",
        "task.qa_fail",
    ]


def test_pr_fail_adds_named_event() -> None:
    assert TaskService._audit_events_for("needs_revision", "pr_reviewer") == [
        "task.needs_revision",
        "task.pr_fail",
    ]


def test_request_changes_adds_named_event_for_cell_pm() -> None:
    assert TaskService._audit_events_for("needs_revision", "cell_pm") == [
        "task.needs_revision",
        "task.request_changes",
    ]


def test_request_changes_adds_named_event_for_main_pm() -> None:
    assert TaskService._audit_events_for("needs_revision", "main_pm") == [
        "task.needs_revision",
        "task.request_changes",
    ]


def test_ceo_reject_to_needs_revision_adds_named_event() -> None:
    assert TaskService._audit_events_for("needs_revision", "ceo") == [
        "task.needs_revision",
        "task.ceo_reject",
    ]


def test_named_event_only_on_needs_revision() -> None:
    # A reviewer role on a non-needs_revision transition gets no named event.
    assert TaskService._audit_events_for("awaiting_pm_review", "pr_reviewer") == [
        "task.awaiting_pm_review"
    ]

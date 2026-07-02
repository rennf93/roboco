"""IntentSpec for the PM `request_changes` verb (S6 postmortem, gap B4).

At awaiting_pm_review the PM previously had NO reject verb — only `complete`
or escalate — so a PM that caught a genuine AC/scope violation at merge review
could only loop `i_am_blocked` + escalate (the live fe-pm block/escalate loop,
2026-07-01). `request_changes` is the merge-level reject: awaiting_pm_review ->
needs_revision with concrete issues, routed like a QA fail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from roboco.foundation.identity import Role
from roboco.foundation.policy.lifecycle import (
    Context,
    Status,
    can_invoke_intent,
    intents_for_role,
    status_after,
)
from roboco.services.gateway.role_config import _CELL_PM_FLOW, _MAIN_PM_FLOW


def test_request_changes_is_a_pm_flow_verb() -> None:
    # Declared for the PM roles, so intents_for_role propagates it into both
    # PM flows automatically — the spec is canon, no role_config edit.
    assert "request_changes" in intents_for_role(Role.CELL_PM)
    assert "request_changes" in intents_for_role(Role.MAIN_PM)
    assert "request_changes" in _CELL_PM_FLOW
    assert "request_changes" in _MAIN_PM_FLOW


def test_request_changes_is_pm_only() -> None:
    for role in (
        Role.DEVELOPER,
        Role.QA,
        Role.DOCUMENTER,
        Role.PR_REVIEWER,
        Role.PRODUCT_OWNER,
        Role.HEAD_MARKETING,
        Role.AUDITOR,
    ):
        assert "request_changes" not in intents_for_role(role), (
            f"{role} must not get request_changes"
        )


def test_request_changes_transitions_pm_review_to_needs_revision() -> None:
    assert status_after("request_changes", Status.AWAITING_PM_REVIEW) == (
        Status.NEEDS_REVISION
    )


def test_request_changes_only_from_awaiting_pm_review() -> None:
    for status in (
        Status.IN_PROGRESS,
        Status.AWAITING_QA,
        Status.AWAITING_PR_REVIEW,
        Status.BLOCKED,
        Status.COMPLETED,
    ):
        assert status_after("request_changes", status) is None


@dataclass
class _Task:
    status: object = Status.AWAITING_PM_REVIEW
    assigned_to: object = None
    task_type: object = "code"
    team: object = "frontend"
    created_by: object = field(default_factory=uuid4)


def test_request_changes_allowed_for_cell_pm_at_pm_review() -> None:
    pm = uuid4()
    task = _Task(assigned_to=pm)
    decision = can_invoke_intent(
        Role.CELL_PM, "request_changes", task, Context(actor_id=pm)
    )
    assert decision.allowed


def test_request_changes_rejected_outside_pm_review() -> None:
    pm = uuid4()
    task = _Task(status=Status.IN_PROGRESS, assigned_to=pm)
    decision = can_invoke_intent(
        Role.CELL_PM, "request_changes", task, Context(actor_id=pm)
    )
    assert not decision.allowed

"""Unit tests for the assignee-capability predicate derived from the
lifecycle role-transition map (``assignee_can_act`` /
``assignee_capability_gap``).

The predicate must stay a pure derivation of ``CLAIM_RULES`` + the atomic
action specs — these tests pin the wedge shapes it exists to refuse
(54e30535: a product-owner id assigned a delivery task it can never act on).
"""

from __future__ import annotations

import pytest
from roboco.foundation.identity import Role
from roboco.foundation.policy.lifecycle import (
    _ATOMIC_ACTIONS,
    CLAIM_RULES,
    Status,
    assignee_can_act,
    assignee_capability_gap,
)


@pytest.mark.parametrize(
    ("role", "status"),
    [
        # Developer claim path + act-in-place statuses.
        (Role.DEVELOPER, Status.PENDING),
        (Role.DEVELOPER, Status.NEEDS_REVISION),
        (Role.DEVELOPER, Status.CLAIMED),
        (Role.DEVELOPER, Status.IN_PROGRESS),
        (Role.DEVELOPER, Status.VERIFYING),
        # QA can act only on the QA queue.
        (Role.QA, Status.AWAITING_QA),
        # Documenter on its queue.
        (Role.DOCUMENTER, Status.AWAITING_DOCUMENTATION),
        # PMs claim pending/needs_revision and act on review/backlog/blocked.
        (Role.CELL_PM, Status.PENDING),
        (Role.CELL_PM, Status.AWAITING_PM_REVIEW),
        (Role.MAIN_PM, Status.BACKLOG),
        (Role.MAIN_PM, Status.BLOCKED),
        # PR reviewer claims the gate.
        (Role.PR_REVIEWER, Status.AWAITING_PR_REVIEW),
        # CEO acts on the approval queue.
        (Role.CEO, Status.AWAITING_CEO_APPROVAL),
    ],
)
def test_role_with_action_on_status_can_act(role: Role, status: Status) -> None:
    assert assignee_can_act(role, status)


@pytest.mark.parametrize(
    ("role", "status"),
    [
        # The 54e30535 wedge shape: board/advisory roles have no delivery
        # verbs — a delivery task assigned to them is unactable.
        (Role.PRODUCT_OWNER, Status.PENDING),
        (Role.HEAD_MARKETING, Status.IN_PROGRESS),
        (Role.AUDITOR, Status.NEEDS_REVISION),
        # Cross-queue assignments: nobody acts outside their lane.
        (Role.DEVELOPER, Status.AWAITING_QA),
        (Role.DEVELOPER, Status.AWAITING_PM_REVIEW),
        (Role.QA, Status.PENDING),
        (Role.QA, Status.AWAITING_DOCUMENTATION),
        (Role.DOCUMENTER, Status.AWAITING_QA),
        (Role.CELL_PM, Status.AWAITING_QA),
        (Role.CEO, Status.PENDING),
    ],
)
def test_role_without_action_on_status_cannot_act(role: Role, status: Status) -> None:
    assert not assignee_can_act(role, status)


@pytest.mark.parametrize("status", list(Status))
def test_terminal_statuses_are_exempt(status: Status) -> None:
    """COMPLETED/CANCELLED have nothing left to act on — any role passes."""
    if status not in (Status.COMPLETED, Status.CANCELLED):
        return
    for role in Role:
        assert assignee_can_act(role, status)


def test_predicate_is_a_derivation_not_a_parallel_map() -> None:
    """Every can-act verdict must be explainable by the existing tables."""
    for role in Role:
        for status in Status:
            if not assignee_can_act(role, status):
                continue
            if status in (Status.COMPLETED, Status.CANCELLED):
                continue  # terminal exemption
            via_claim = status in CLAIM_RULES.get(role, frozenset())
            via_action = any(
                role in spec.allowed_roles and status in spec.source_statuses
                for name, spec in _ATOMIC_ACTIONS.items()
                if name
                not in ("claim", "cancel")  # union/escape-hatch actions excluded
            )
            assert via_claim or via_action, (role, status)


def test_gap_message_names_role_and_status() -> None:
    gap = assignee_capability_gap(Role.PRODUCT_OWNER, Status.PENDING)
    assert gap is not None
    assert "product_owner" in gap
    assert "pending" in gap


def test_gap_is_none_when_role_can_act() -> None:
    assert assignee_capability_gap(Role.DEVELOPER, Status.PENDING) is None

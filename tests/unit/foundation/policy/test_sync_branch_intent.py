"""IntentSpec for the dev `sync_branch` verb (multi-level sequencing Phase B1).

Raw shell git is denied to agents by design (the `Bash(git:*)` base deny), so a
developer whose branch has fallen behind its base had no gate-level way to
rebase — only the CEO/PM-only `/rebase` HTTP route. `sync_branch` is the dev
verb that wraps the git rebase through the gate (traced + evidenced), so the
"everything goes through the gates" invariant holds. These tests lock the spec
declaration: dev-only, ownership-gated, composes nothing (git-only, no DB
transition), and present in the dev flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest
from roboco.foundation.identity import Role
from roboco.foundation.policy.lifecycle import (
    Context,
    Decision,
    can_invoke_intent,
    intents_for_role,
)
from roboco.services.gateway.role_config import _DEV_FLOW


def test_sync_branch_is_a_dev_flow_verb() -> None:
    # Declared with _DEV_ROLES, so intents_for_role propagates it into the dev
    # flow automatically — no role_config edit needed (the spec is canon).
    assert "sync_branch" in intents_for_role(Role.DEVELOPER)
    assert "sync_branch" in _DEV_FLOW


def test_sync_branch_is_dev_only() -> None:
    # QA / documenter / PM cannot call it — it's a developer's branch-sync verb.
    for role in (Role.QA, Role.DOCUMENTER, Role.CELL_PM, Role.MAIN_PM):
        assert "sync_branch" not in intents_for_role(role), (
            f"{role} must not get sync_branch"
        )


@dataclass
class _Task:
    assigned_to: object = None
    # An active dev state by default so the ownership / role tests don't trip the
    # source-status gate (which they're not exercising). Rejection tests pass an
    # explicit non-active status.
    status: object = "in_progress"


def test_sync_branch_requires_ownership() -> None:
    # PRECONDITION_OWNERSHIP (rejection_kind='not_authorized') gates it: a task
    # assigned to another agent rejects as not_authorized, not a tracing gap.
    owner = uuid4()
    other = uuid4()
    task = _Task(assigned_to=other)
    decision = can_invoke_intent(
        Role.DEVELOPER, "sync_branch", task, Context(actor_id=owner)
    )
    assert not decision.allowed
    assert decision.rejection_kind == "not_authorized"


def test_sync_branch_allowed_when_owner() -> None:
    # composes=() and the only preconditions are ownership + an active
    # source-status, so the owner of an in_progress task passes the spec gate.
    # (The choreographer handler does the git work + branch/base guards
    # separately.)
    owner = uuid4()
    task = _Task(assigned_to=owner, status="in_progress")
    decision = can_invoke_intent(
        Role.DEVELOPER, "sync_branch", task, Context(actor_id=owner)
    )
    assert isinstance(decision, Decision)
    assert decision.allowed


@pytest.mark.parametrize(
    "status",
    ["completed", "cancelled", "paused", "blocked", "awaiting_qa", "pending"],
)
def test_sync_branch_rejects_non_active_status(status: str) -> None:
    """#50: sync_branch composes=() so without a source-status gate the spec
    accepted a terminal / paused / blocked / reviewing task and the handler
    rebased a branch whose task was no longer the dev's to move. The owner of a
    non-active task is now rejected as invalid_state."""
    owner = uuid4()
    task = _Task(assigned_to=owner, status=status)
    decision = can_invoke_intent(
        Role.DEVELOPER, "sync_branch", task, Context(actor_id=owner)
    )
    assert not decision.allowed
    assert decision.rejection_kind == "invalid_state"


@pytest.mark.parametrize("status", ["claimed", "verifying", "needs_revision"])
def test_sync_branch_allows_active_dev_status(status: str) -> None:
    """The active dev working states (claimed / in_progress / verifying /
    needs_revision) are exactly where a behind-base rebase is meaningful."""
    owner = uuid4()
    task = _Task(assigned_to=owner, status=status)
    decision = can_invoke_intent(
        Role.DEVELOPER, "sync_branch", task, Context(actor_id=owner)
    )
    assert decision.allowed


def test_sync_branch_unknown_to_other_role_rejects() -> None:
    # A role not in allowed_roles is rejected as not_authorized (role gating).
    decision = can_invoke_intent(Role.QA, "sync_branch", _Task(), Context())
    assert not decision.allowed
    assert decision.rejection_kind == "not_authorized"

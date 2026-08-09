"""A stranded agent's `i_am_blocked` after an admin status override must
point at the real exit: unclaim() then give_me_work().

Live incident: the CEO's admin status override (PATCH /api/tasks/{id},
force=true) can move a task a live agent still holds into a review-queue
state such as `needs_revision` (`_REVIEW_QUEUE_STATES`), clearing
`active_claimant_id` DB-side without ever stopping the agent's container.
The agent's natural first move, `i_am_blocked`, is spec-gated to
`in_progress` sources, so it rejected with a generic "call give_me_work()"
hint that never mentioned releasing the now-stale claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from roboco.foundation.identity import Role
from roboco.foundation.policy.lifecycle import Context, can_invoke_intent


@dataclass
class _Task:
    status: object = "needs_revision"
    assigned_to: object = None
    task_type: object = "code"
    team: object = "backend"
    created_by: object = field(default_factory=uuid4)


def test_blocked_from_needs_revision_points_at_unclaim() -> None:
    dev = uuid4()
    task = _Task(assigned_to=dev)
    decision = can_invoke_intent(
        Role.DEVELOPER, "i_am_blocked", task, Context(actor_id=dev)
    )
    assert not decision.allowed
    remediate = decision.remediate or ""
    assert "unclaim()" in remediate
    assert "give_me_work()" in remediate
    assert "needs_revision" in remediate


def test_blocked_from_needs_revision_does_not_reuse_generic_hint() -> None:
    """The generic "call give_me_work() to find a task in [...]" hint never
    named the exit — the new case must replace it, not just append to it."""
    dev = uuid4()
    task = _Task(assigned_to=dev)
    decision = can_invoke_intent(
        Role.DEVELOPER, "i_am_blocked", task, Context(actor_id=dev)
    )
    remediate = decision.remediate or ""
    assert "find a task in" not in remediate

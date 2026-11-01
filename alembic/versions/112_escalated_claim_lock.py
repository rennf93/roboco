"""Give each blocked task's claim lock to the agent that owns it.

``TaskService.apply_escalation`` reassigned an escalated task to the
escalation target (``assigned_to`` and ``claimed_by``) but left
``active_claimant_id`` on the agent that escalated it. The content-write
gate admits only the active claimant, so the target's ``note`` / ``dm`` /
``notify`` on the blocked task it now owned were refused (19 refusals on
2026-10-28 and 2026-11-01). The code now moves the lock with the task; this
heals the rows escalated before that shipped, so a PM respawned on one of
them can journal its decision there.

Only BLOCKED rows with one owner (``assigned_to = claimed_by``) that does
not hold the lock change. A dependency block never reassigns, so its owner
already holds the lock; a reassigned or released row is not BLOCKED.

The downgrade does nothing: the replaced values were stale locks pointing at
agents that no longer owned the task, and putting them back would only
refuse the owner's writes again.

Revision ID: 112_escalated_claim_lock
Revises: 111_ci_runs_reading_key
Create Date: 2026-11-01
"""

from __future__ import annotations

from alembic import op

revision = "112_escalated_claim_lock"
down_revision = "111_ci_runs_reading_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE tasks
        SET active_claimant_id = claimed_by
        WHERE status = 'blocked'
          AND claimed_by IS NOT NULL
          AND assigned_to = claimed_by
          AND active_claimant_id IS DISTINCT FROM claimed_by
        """
    )


def downgrade() -> None:
    """Keep the healed locks: the old values only refused the owner."""

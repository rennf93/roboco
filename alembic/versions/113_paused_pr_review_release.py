"""Release the PR-review tasks a reviewer's ``i_am_idle`` left paused.

``claim_pr_review`` moves a review task to in_progress; ``i_am_idle`` used
to PAUSE it. A reviewer cannot resume a paused task (``i_will_work_on`` is
developer-only), and ``paused_tasks_guard`` refused every later
``claim_pr_review`` while it was held, so the review lane stayed dead (41
refusals for pr-reviewer-1 after 72e5176a, 2026-11-02). The code now sends an
in_progress PR-review task back to PENDING on ``i_am_idle``; this heals the
rows paused before that shipped.

A paused review with a newer non-terminal review of the same project and PR is
superseded (a new PR head opens a fresh review by design) and is cancelled.
Any other paused review goes back to PENDING unclaimed and unassigned, the
state a fresh review task has, which the dispatcher requires to spawn the
reviewer.

The downgrade does nothing: a paused review nobody can resume is the bug.

Revision ID: 113_paused_pr_review_release
Revises: 112_escalated_claim_lock
Create Date: 2026-11-03
"""

from __future__ import annotations

from alembic import op

revision = "113_paused_pr_review_release"
down_revision = "112_escalated_claim_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE tasks AS t
        SET status = 'cancelled', assigned_to = NULL,
            claimed_by = NULL,
            claimed_at = NULL,
            active_claimant_id = NULL,
            last_heartbeat_at = NULL
        WHERE t.status = 'paused'
          AND t.source IN ('external_pr', 'internal_pr')
          AND EXISTS (
            SELECT 1 FROM tasks n
            WHERE n.project_id = t.project_id
              AND n.pr_number = t.pr_number
              AND n.source IN ('external_pr', 'internal_pr')
              AND n.created_at > t.created_at
              AND n.status NOT IN ('completed', 'cancelled')
          )
        """
    )
    op.execute(
        """
        UPDATE tasks
        SET status = 'pending', assigned_to = NULL,
            claimed_by = NULL,
            claimed_at = NULL,
            active_claimant_id = NULL,
            last_heartbeat_at = NULL
        WHERE status = 'paused'
          AND source IN ('external_pr', 'internal_pr')
        """
    )


def downgrade() -> None:
    """Keep the healed rows: a paused review nobody can resume is the bug."""

"""Doctrine-version stamp on agent_spawn_sessions, for the eval harness.

The eval harness (roboco/eval/) scores a (role, model/provider config) cohort
by replaying golden tasks through a real agent spawn. To attribute a quality
delta to a prompt/doctrine change (fable-mode, ponytail, a team-prompt edit,
...) the resulting spawn session needs to carry a fingerprint of exactly what
system prompt it ran with — otherwise two cohort runs are only comparable if
the operator remembers to keep everything else byte-for-byte identical.

``doctrine_version`` is a short hash of the composed system prompt (base +
role + team + identity + doctrine layers) for that spawn, stamped at
``_finalize_spawn_session`` in roboco/runtime/orchestrator.py — NOT at
``_record_spawn_session`` (spawn creation). The composed prompt string itself
is not passed through the AgentConfig the finalize call site holds, but the
file it was written to (``config.blueprint_path``, from
``_generate_composed_prompt``) is still on disk and unchanged at finalize
time (nothing in the spawn/stop path deletes it), so the finalize call reads
it back and hashes it there. Every provider gets one — ``_prepare_agent_spawn``
composes and writes the blueprint unconditionally, before provider/route
resolution, so GROK agents carry a real blueprint file too, same as Claude.
Nullable + additive: every existing row, and any row where the read
genuinely fails (a provider-parked stub instance that never actually
spawned — ``blueprint_path=Path()`` — an evicted temp dir, ...), simply gets
NULL — a pure quality-of-life addition to the sessions the eval harness
scores, never a hard requirement of the spawn/stop path.

Revision ID: 081_doctrine_version
Revises: 080_task_project_budgets
Create Date: 2026-07-22

Note: re-chained onto 080_task_project_budgets (sibling PRs #652/#654 own
079/080 at this branch's base commit, da4d9b33, where 078 was the head);
080 does not exist in this worktree, so the local migration-graph/enum-parity
tests are expected to fail here until this branch integrates alongside its
siblings — the same expected-failure posture the budgets sibling reported.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "081_doctrine_version"
down_revision = "080_task_project_budgets"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_spawn_sessions",
        sa.Column("doctrine_version", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_spawn_sessions", "doctrine_version")

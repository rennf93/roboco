"""No-op: skills alignment was completed statically in agents_config.py.

Originally tried to UPDATE agents.skills (a JSON column), but the agents
table never had a `skills` column — only `capabilities` (ARRAY(String)).
The intended substitution (`qa_review` -> `code_review`) was already done
in `roboco/agents_config.py` via direct edit. This migration is preserved
as a no-op so any database stamped at this revision keeps a valid chain
position.

Revision ID: 008_align_skills
Revises: 007_gateway_triggers_table
Create Date: 2026-05-01
"""

from __future__ import annotations

revision = "008_align_skills"
down_revision = "007_gateway_triggers_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op. Skill alignment was performed statically; see module docstring."""
    return None


def downgrade() -> None:
    """No-op."""
    return None

"""Align agent skills to a canonical set.

Standardizes QA skills on `code_review` (drops `qa_review`) by substituting
old skill ids in the existing agents.skills JSON column. Preserves all other
skills + agent-specific customizations. Idempotent: safe to re-run; only
updates rows that actually need substitution.

Revision ID: 008_align_skills
Revises: 007_gateway_triggers_table
Create Date: 2026-05-01
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "008_align_skills"
down_revision = "007_gateway_triggers_table"
branch_labels = None
depends_on = None


# Canonical skill substitutions: old -> new.
# Extend this dict for future skill renames; the migration logic stays the same.
SKILL_SUBSTITUTIONS = {
    "qa_review": "code_review",
}


def _substitute(
    skills_value: list | str | None,
    mapping: dict[str, str],
) -> tuple[list, bool]:
    """Apply substitutions to a skills value (list of dicts or list of strings).

    Returns (new_list, changed).
    """
    if skills_value is None:
        return [], False
    if isinstance(skills_value, str):
        skills: list = json.loads(skills_value)
    else:
        skills = list(skills_value)
    new_skills: list = []
    changed = False
    for original in skills:
        if isinstance(original, dict):
            old_id = original.get("id")
            if old_id in mapping:
                new_skills.append({**original, "id": mapping[old_id]})
                changed = True
                continue
        elif isinstance(original, str) and original in mapping:
            new_skills.append(mapping[original])
            changed = True
            continue
        new_skills.append(original)
    return new_skills, changed


def upgrade() -> None:
    """Substitute old skill ids in agents.skills; preserves everything else."""
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, skills FROM agents")).fetchall()
    for row in rows:
        new_skills, changed = _substitute(row.skills, SKILL_SUBSTITUTIONS)
        if changed:
            bind.execute(
                sa.text("UPDATE agents SET skills = :s WHERE id = :id"),
                {"s": json.dumps(new_skills), "id": row.id},
            )


def downgrade() -> None:
    """Reverse substitution: code_review -> qa_review for QA agents only.

    Limited to role='qa' so we don't accidentally rename `code_review` skills
    that legitimately belong to non-QA agents (e.g., developers also have
    code_review as a skill).
    """
    bind = op.get_bind()
    inverse = {v: k for k, v in SKILL_SUBSTITUTIONS.items()}
    rows = bind.execute(
        sa.text("SELECT id, skills FROM agents WHERE role = 'qa'")
    ).fetchall()
    for row in rows:
        new_skills, changed = _substitute(row.skills, inverse)
        if changed:
            bind.execute(
                sa.text("UPDATE agents SET skills = :s WHERE id = :id"),
                {"s": json.dumps(new_skills), "id": row.id},
            )

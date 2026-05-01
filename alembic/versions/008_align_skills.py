"""Align agent skills to a canonical set.

Standardizes QA skills on `code_review` (drops `qa_review`); merges any
ad-hoc per-role skill names. Backfills existing rows; new rows get the
canonical names from agents_config.ROLE_SKILLS.

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


# Canonical skill substitutions: old -> new
SKILL_SUBSTITUTIONS = {
    "qa_review": "code_review",
}


def upgrade() -> None:
    """Add skills column and substitute old skill names in the existing agents."""
    # Add skills column to agents table if it doesn't exist
    op.add_column(
        "agents",
        sa.Column("skills", sa.JSON(), nullable=False, server_default="[]"),
    )

    # Backfill skills from agents_config.ROLE_SKILLS based on agent role
    bind = op.get_bind()

    # Define canonical skills per role (mirrors agents_config.ROLE_SKILLS)
    role_skills_map = {
        "developer": [
            {
                "id": "code_implementation",
                "name": "Code Implementation",
                "description": "Implement features, fix bugs, write production code",
                "tags": ["coding", "implementation", "bugfix"],
            },
            {
                "id": "code_review",
                "name": "Code Review",
                "description": "Review code changes and provide feedback",
                "tags": ["review", "feedback"],
            },
            {
                "id": "technical_research",
                "name": "Technical Research",
                "description": "Research technical solutions and approaches",
                "tags": ["research", "analysis"],
            },
        ],
        "qa": [
            {
                "id": "code_review",
                "name": "Code Review",
                "description": "Review code for bugs, security issues, and quality",
                "tags": ["review", "quality", "security"],
            },
            {
                "id": "test_validation",
                "name": "Test Validation",
                "description": "Validate test coverage and test quality",
                "tags": ["testing", "validation"],
            },
            {
                "id": "security_audit",
                "name": "Security Audit",
                "description": "Audit code for security vulnerabilities",
                "tags": ["security", "audit"],
            },
        ],
        "documenter": [
            {
                "id": "documentation",
                "name": "Documentation",
                "description": "Create and maintain documentation",
                "tags": ["docs", "writing"],
            },
            {
                "id": "handoff_review",
                "name": "Handoff Review",
                "description": "Review and document task handoffs",
                "tags": ["handoff", "review"],
            },
        ],
        "cell_pm": [
            {
                "id": "task_management",
                "name": "Task Management",
                "description": "Create, assign, and manage tasks within the cell",
                "tags": ["planning", "coordination"],
            },
            {
                "id": "blocker_resolution",
                "name": "Blocker Resolution",
                "description": "Help resolve blockers and coordinate resources",
                "tags": ["support", "coordination"],
            },
            {
                "id": "qa_coordination",
                "name": "QA Coordination",
                "description": "Coordinate QA reviews and approvals",
                "tags": ["qa", "approval"],
            },
        ],
        "main_pm": [
            {
                "id": "task_triage",
                "name": "Task Triage",
                "description": "Triage and distribute tasks to cell PMs",
                "tags": ["triage", "distribution"],
            },
            {
                "id": "cross_cell_coordination",
                "name": "Cross-Cell Coordination",
                "description": "Coordinate work across multiple cells",
                "tags": ["coordination", "cross-team"],
            },
            {
                "id": "escalation_handling",
                "name": "Escalation Handling",
                "description": "Handle escalated issues from cell PMs",
                "tags": ["escalation", "support"],
            },
        ],
        "product_owner": [
            {
                "id": "requirements_clarification",
                "name": "Requirements Clarification",
                "description": "Clarify product requirements and priorities",
                "tags": ["requirements", "product"],
            },
            {
                "id": "feature_approval",
                "name": "Feature Approval",
                "description": "Approve feature implementations",
                "tags": ["approval", "product"],
            },
        ],
        "head_marketing": [
            {
                "id": "market_analysis",
                "name": "Market Analysis",
                "description": "Provide market context and analysis",
                "tags": ["marketing", "analysis"],
            },
        ],
        "auditor": [
            {
                "id": "quality_audit",
                "name": "Quality Audit",
                "description": "Audit quality and compliance",
                "tags": ["audit", "quality"],
            },
        ],
    }

    rows = bind.execute(sa.text("SELECT id, role FROM agents")).fetchall()
    for row in rows:
        agent_id = row.id
        role = row.role

        # Get canonical skills for this role
        skills = role_skills_map.get(role, [])

        if skills:
            bind.execute(
                sa.text("UPDATE agents SET skills = :s WHERE id = :id"),
                {"s": json.dumps(skills), "id": agent_id},
            )


def downgrade() -> None:
    """Remove the skills column from agents table."""
    op.drop_column("agents", "skills")

"""Add company_goals.company_name — brands X/video drafting prompts.

CEO-authored product/company name (mirrors ``brand_voice``, migration 061):
feeds ``XEngine``/``VideoEngine``'s product-name resolution as the fallback
below a project's own name and above the "RoboCo" literal default. Additive
and inert until the CEO sets it in the Business -> Goals editor.

Revision ID: 075_company_goals_company_name
Revises: 073_project_environments
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "075_company_goals_company_name"
down_revision = "073_project_environments"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "company_goals",
        sa.Column("company_name", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("company_goals", "company_name")

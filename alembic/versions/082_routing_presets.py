"""Routing presets — named, full snapshots of the AI-routing state.

Lets an operator save the current routing state (mode + every
`model_assignments` row: GLOBAL / plain ROLE / compound ROLE(":"complexity)
cost-tier overrides / AGENT_SLUG pins) under a name and re-apply it later in
one call, instead of re-picking every per-agent Select. ``payload`` mirrors
exactly what ``GET /providers`` + ``GET /providers/complexity-overrides``
already serve, so a preset is "what the card currently shows" — see
``ModelRoutingService.save_routing_preset`` / ``apply_routing_preset``
(roboco/services/llm.py). Pure additive new table; no backfill, no data
migration — applying a preset is the only thing that ever mutates
``model_assignments`` as a side effect, and only on an explicit call.

Revision ID: 082_routing_presets
Revises: 081_doctrine_version
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "082_routing_presets"
down_revision = "081_doctrine_version"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.create_table(
        "routing_presets",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("name", name="uq_routing_presets_name"),
    )


def downgrade() -> None:
    op.drop_table("routing_presets")

"""Add verb_latency_samples table for per-verb HTTP duration telemetry.

Persists one row per gateway-verb invocation from
``RequestLoggingMiddleware`` (success, error, and timeout paths) so
``MetricsService.get_verb_latency_stats`` can aggregate p50/p95 per verb
via ``PERCENTILE_CONT`` and ``SentinelEngine`` can surface slow verbs.

Revision ID: 094_verb_latency_samples
Revises: 093_playbook_source_program
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "094_verb_latency_samples"
down_revision = "093_playbook_source_program"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.create_table(
        "verb_latency_samples",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("verb", sa.String(length=80), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_verb_latency_samples_verb", "verb_latency_samples", ["verb"]
    )
    op.create_index(
        "ix_verb_latency_samples_created_at", "verb_latency_samples", ["created_at"]
    )
    op.create_index(
        "ix_verb_latency_samples_verb_created",
        "verb_latency_samples",
        ["verb", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_verb_latency_samples_verb_created", table_name="verb_latency_samples"
    )
    op.drop_index(
        "ix_verb_latency_samples_created_at", table_name="verb_latency_samples"
    )
    op.drop_index("ix_verb_latency_samples_verb", table_name="verb_latency_samples")
    op.drop_table("verb_latency_samples")
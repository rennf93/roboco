"""Per-project video-engine opt-in column.

The video engine is armed by the global ``ROBOCO_VIDEO_ENGINE_ENABLED`` flag,
but authoring writes a HyperFrames composition into a project's ``motion/``
dir, so a project opts in via ``video_engine_enabled``. Additive and
default-off, mirroring ``ci_watch_enabled`` (migration 048): existing projects
keep today's behavior (no video authoring) until the operator flips it in the
panel.

Revision ID: 063_video_engine_project_toggle
Revises: 062_tiktok_credentials
Create Date: 2026-07-06

NOTE: revision id is 25 chars — alembic's ``alembic_version.version_num`` is
``VARCHAR(32)`` and a longer id raises at record time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "063_video_engine_project_toggle"
down_revision = "062_tiktok_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "video_engine_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "video_engine_enabled")

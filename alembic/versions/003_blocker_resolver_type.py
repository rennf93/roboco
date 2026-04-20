"""Add blocker_resolver_type to tasks.

Revision ID: 003_blocker_resolver_type
Revises: 002_persistence_tables
Create Date: 2026-04-19

Adds `tasks.blocker_resolver_type` so the dispatcher can tell the difference
between blocks an agent can resolve (dispatcher may respawn) and blocks that
need human intervention (dispatcher must NOT respawn). Without this, agents
keep churning on HITL-blocked tasks and burning tokens.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "003_blocker_resolver_type"
down_revision = "002_persistence_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the enum type — names match SA's default Enum(PyEnum) binding
    # (it serializes enum members by NAME, which is uppercase per PEP 8).
    blocker_resolver_enum = sa.Enum(
        "AGENT",
        "HUMAN",
        name="blockerresolvertype",
    )
    blocker_resolver_enum.create(op.get_bind(), checkfirst=True)

    # Add nullable column — NULL means "not applicable" (task isn't blocked)
    # or "legacy block before this migration". Dispatcher treats NULL the
    # same as AGENT (old default behavior) to preserve back-compat.
    op.add_column(
        "tasks",
        sa.Column(
            "blocker_resolver_type",
            blocker_resolver_enum,
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "blocker_resolver_type")
    sa.Enum(name="blockerresolvertype").drop(op.get_bind(), checkfirst=True)

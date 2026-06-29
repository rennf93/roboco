"""Add products + product_projects tables and additive tasks.product_id.

Introduces the Product entity (a per-cell Project mapping) for board->cells
routing. tasks.product_id is additive + nullable (ON DELETE RESTRICT);
project_id is unchanged. product_projects.team reuses the existing Postgres
"team" enum (create_type=False), unique on (product_id, team).

Revision ID: 016_add_products_product_id
Revises: 015_drop_task_execution_outputs
Create Date: 2026-05-31

Note: the revision id is kept to 27 chars because alembic_version.version_num
is VARCHAR(32); the spec's longer literal ("016_add_products_and_task_product_id",
36 chars) overflows that column and cannot be stamped.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "016_add_products_product_id"
down_revision = "015_drop_task_execution_outputs"
branch_labels = None
depends_on = None

# Reuse the existing Postgres "team" enum in place (created in 001_initial_schema,
# widened since by later migrations). ``create_type=False`` MUST be set on the
# postgres-native ``postgresql.ENUM``: it's that class's ``create_type`` attribute
# that ``_check_for_name_in_memos`` reads to suppress the redundant ``CREATE TYPE``
# on ``op.create_table`` (checkfirst=False, so the has_type probe is skipped). On
# the generic ``sa.Enum`` the kwarg is silently dropped, so the CREATE TYPE would
# fire and crash a boot against a DB where the enum pre-exists ("type 'team'
# already exists"). The member list is inert under create_type=False (it never
# creates/alters the type), so it reflects the enum as it stood at 016's time,
# not the later-widened set. See project_migration_enum_create_type_gotcha.
_TEAM_ENUM = postgresql.ENUM(
    "backend",
    "frontend",
    "ux_ui",
    "main_pm",
    "board",
    "marketing",
    name="team",
    create_type=False,
)

# Literal (non-interpolated) emptiness probes: the orphan table names are fixed
# constants, so these are constant SQL strings with no injection surface.
_ORPHAN_EMPTY_CHECKS = {
    "product_projects": sa.text("SELECT count(*) FROM product_projects"),
    "products": sa.text("SELECT count(*) FROM products"),
}


def _drop_orphan_create_all_tables() -> None:
    """Drop EMPTY orphan products/product_projects left by the old init_db
    create_all fallback (removed 2026-06-01).

    Before that fallback was removed, a failed boot of this migration rolled
    back and create_all re-created bare products/product_projects tables; the
    next boot's create_table below then failed with "relation already exists",
    looping forever. Dropping the EMPTY orphans here lets the migration apply
    cleanly and self-heals such a DB on the next deploy. Skipped in offline
    (--sql) mode (no live DB to inspect); refuses to drop a table holding rows.
    """
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    for orphan in ("product_projects", "products"):  # child before parent
        exists = bind.execute(
            sa.text("SELECT to_regclass(:qualified)"),
            {"qualified": f"public.{orphan}"},
        ).scalar()
        if exists is None:
            continue
        rows = bind.execute(_ORPHAN_EMPTY_CHECKS[orphan]).scalar() or 0
        if rows:
            raise RuntimeError(
                f"migration 016: refusing to drop non-empty orphan table "
                f"{orphan!r} ({rows} rows); investigate manually"
            )
        op.drop_table(orphan)


def upgrade() -> None:
    # Heal DBs polluted by the old create_all fallback before creating tables.
    _drop_orphan_create_all_tables()

    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "product_projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("team", _TEAM_ENUM, nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.UniqueConstraint(
            "product_id", "team", name="uq_product_projects_product_team"
        ),
    )

    op.add_column(
        "tasks",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_tasks_product_id_products",
        "tasks",
        "products",
        ["product_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_tasks_product_status", "tasks", ["product_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_tasks_product_status", table_name="tasks")
    # FK name follows the project's metadata naming convention
    # (fk_%(table)s_%(column)s_%(referred_table)s), not Postgres's default
    # "tasks_product_id_fkey".
    op.drop_constraint("fk_tasks_product_id_products", "tasks", type_="foreignkey")
    op.drop_column("tasks", "product_id")
    op.drop_table("product_projects")
    op.drop_table("products")

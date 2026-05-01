"""Provider routing: provider_configs + model_assignments.

Revision ID: 004_provider_routing
Revises: 003_blocker_resolver_type
Create Date: 2026-04-21

Adds two tables so agents can be routed per-role / per-agent to different
model providers (Anthropic via mounted ~/.claude, Ollama Cloud via
ANTHROPIC_BASE_URL+AUTH_TOKEN env injection).

- `provider_configs`: one row per logical provider. The Anthropic default
  is seeded with no base_url / no token — it's a pointer-only row; auth
  stays in the agent container's mounted ~/.claude.
- `model_assignments`: scope (`global` | `role` | `agent_slug`) →
  (provider, model_name). Unique on (scope, scope_value) with NULLS NOT
  DISTINCT so the single `global` row can't be duplicated.

Zero rows in `model_assignments` leaves every spawn on the legacy
`ROLE_MODEL_MAP` path — fully backward-compatible.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "004_provider_routing"
down_revision = "003_blocker_resolver_type"
branch_labels = None
depends_on = None


# SQLAlchemy's default Enum binding serialises members by Python NAME
# (uppercase), matching the 003_blocker_resolver_type convention.
_PROVIDER_TYPES = ("ANTHROPIC", "OLLAMA_CLOUD", "OPENAI", "LOCAL")
_ASSIGNMENT_SCOPES = ("GLOBAL", "ROLE", "AGENT_SLUG")


def upgrade() -> None:
    # `checkfirst=True` on SA's ENUM creation has been unreliable across
    # PG versions; use raw `DO $$` + `EXCEPTION duplicate_object` so the
    # migration is safely re-runnable on a DB that already has the types
    # from a prior half-applied attempt.
    op.execute(
        sa.text(
            """
            DO $$ BEGIN
                CREATE TYPE modelprovider AS ENUM (
                    'ANTHROPIC', 'OLLAMA_CLOUD', 'OPENAI', 'LOCAL'
                );
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$ BEGIN
                CREATE TYPE assignmentscope AS ENUM (
                    'GLOBAL', 'ROLE', 'AGENT_SLUG'
                );
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
            """
        )
    )

    # Reference the pre-existing enums from the table columns without
    # re-emitting their DDL. `create_type=False` is the critical flag.
    provider_enum = postgresql.ENUM(
        *_PROVIDER_TYPES, name="modelprovider", create_type=False
    )
    scope_enum = postgresql.ENUM(
        *_ASSIGNMENT_SCOPES, name="assignmentscope", create_type=False
    )

    # Defensive: if a prior attempt half-created these tables, drop them
    # clean before re-creating. Enum types survive this (they're owned by
    # the database, not the tables).
    op.execute(sa.text("DROP TABLE IF EXISTS model_assignments CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS provider_configs CASCADE"))

    op.create_table(
        "provider_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("type", provider_enum, nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("auth_token_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint("name", name="uq_provider_configs_name"),
    )
    op.create_index(
        "ix_provider_configs_name",
        "provider_configs",
        ["name"],
        unique=False,
    )
    op.create_index(
        "ix_provider_configs_enabled",
        "provider_configs",
        ["enabled"],
        unique=False,
    )

    op.create_table(
        "model_assignments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("scope", scope_enum, nullable=False),
        sa.Column("scope_value", sa.String(length=100), nullable=True),
        sa.Column(
            "provider_config_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["provider_config_id"],
            ["provider_configs.id"],
            ondelete="RESTRICT",
        ),
    )
    # NULLS NOT DISTINCT is PG 15+. roboco runs pgvector on PG 16, so fine.
    # This stops the `global` row (scope_value=NULL) from being duplicated.
    op.create_index(
        "ux_model_assignments_scope_key",
        "model_assignments",
        ["scope", "scope_value"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_model_assignments_provider",
        "model_assignments",
        ["provider_config_id"],
        unique=False,
    )

    # Seed BOTH providers so the Settings UI is zero-setup: the user never
    # "creates a provider" — they just pick a mode and (if using Ollama)
    # paste the API key.
    #
    #   * Anthropic — pointer-only. base_url + token stay NULL; spawn-time
    #     env injection is skipped and the container uses its mounted
    #     ~/.claude auth just like today.
    #
    #   * Ollama Cloud — pre-seeded disabled. The key-input endpoint
    #     (`PUT /api/v1/providers/ollama-key`) flips enabled=true and
    #     stores the Fernet-encrypted token when the user saves their key.
    op.execute(
        sa.text(
            """
            INSERT INTO provider_configs
                (id, name, type, base_url, auth_token_encrypted, enabled, created_at)
            VALUES
                (
                    gen_random_uuid(),
                    'Anthropic (default)',
                    'ANTHROPIC',
                    NULL,
                    NULL,
                    true,
                    now()
                ),
                (
                    gen_random_uuid(),
                    'Ollama Cloud',
                    'OLLAMA_CLOUD',
                    'https://ollama.com',
                    NULL,
                    false,
                    now()
                )
            """
        )
    )


def downgrade() -> None:
    # `if_exists=True` makes the downgrade safe on a partially-applied
    # schema (e.g., an earlier upgrade that half-succeeded), since
    # alembic `drop_table` doesn't take a checkfirst flag directly.
    op.execute("DROP INDEX IF EXISTS ix_model_assignments_provider")
    op.execute("DROP INDEX IF EXISTS ux_model_assignments_scope_key")
    op.execute("DROP TABLE IF EXISTS model_assignments")

    op.execute("DROP INDEX IF EXISTS ix_provider_configs_enabled")
    op.execute("DROP INDEX IF EXISTS ix_provider_configs_name")
    op.execute("DROP TABLE IF EXISTS provider_configs")

    postgresql.ENUM(name="assignmentscope").drop(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM(name="modelprovider").drop(op.get_bind(), checkfirst=True)

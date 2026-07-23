"""Idempotently seed the Gemini (Google) provider row.

The ``modelprovider`` enum carries ``'gemini'`` as of migration 084. This
migration seeds the corresponding ``provider_configs`` row so the Settings UI
can list it for role/agent model assignment.

Unlike Grok's row (migration 039), Gemini has no API-key mode: the CLI
authenticates from a mounted OAuth credential (``~/.gemini/oauth_creds.json``),
never a base URL / bearer token, so both columns stay NULL permanently. The
row starts disabled; an operator enables it once the host OAuth credential is
in place (``ROBOCO_HOST_GEMINI_DIR``). ON CONFLICT (name) DO NOTHING keeps
this safe to re-run.

RE-CHAIN CAVEAT: renumbered 083->085 (was 083 in this task's original
checkout at head 081) to merge after two sibling worktrees' 082 (routing) /
083 (codex `seed_openai_provider`) — see 084_modelprovider_gemini.py's
docstring for the full note. Neither sibling exists in this checkout, so this
worktree's own migration-graph-integrity / enum-parity tests fail on the
missing revisions until the real three-way merge lands.

Revision ID: 085_seed_gemini_provider
Revises: 084_modelprovider_gemini
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "085_seed_gemini_provider"
down_revision = "084_modelprovider_gemini"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO provider_configs
                (id, name, type, base_url, auth_token_encrypted, enabled, created_at)
            VALUES
                (
                    gen_random_uuid(),
                    'Gemini (Google)',
                    'gemini',
                    NULL,
                    NULL,
                    false,
                    now()
                )
            ON CONFLICT (name) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Drop model_assignments pointing at the Gemini row first to avoid a FK
    # RESTRICT violation on provider_configs.id.
    op.execute(
        sa.text(
            "DELETE FROM model_assignments "
            "WHERE provider_config_id IN ("
            "    SELECT id FROM provider_configs WHERE name = 'Gemini (Google)'"
            ")"
        )
    )
    op.execute(sa.text("DELETE FROM provider_configs WHERE name = 'Gemini (Google)'"))

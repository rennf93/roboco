"""Idempotently seed the Codex (OpenAI) provider row.

The ``modelprovider`` enum has carried ``'openai'`` since migration
`004_provider_routing`, but no row was ever seeded for it (unlike GROK's
`039_seed_grok_provider`) — so any assignment of a catalog model whose
`provider_type` is OPENAI (`gpt-5.3-codex`) raised `NotFoundError` out of
`ModelRoutingService._get_seeded_provider`, making the whole Codex provider
unreachable via the panel the moment an operator tried to route an agent to
it. This migration is that missing seed.

Unlike GROK's row (seeded `enabled=false`, flipped to `true` only by the
dedicated `apply_mode="grok"` write path), this row seeds `enabled=true`
directly: there is no `apply_mode="codex"` button — the only way to route to
Codex is "mix" mode's per-agent picker, which has no equivalent enable step.
Seeding disabled would leave `resolve_for_agent` silently falling back to the
legacy Anthropic path forever (`resolved.provider.enabled` gates the route),
reproducing the exact "silently unreachable" failure this migration exists to
fix. Codex authenticates via a mounted ChatGPT-subscription `~/.codex`
directory (see `roboco.llm.providers.codex.CodexCliProvider`), not a stored
API key, so there is no secret to withhold behind a disabled row anyway —
`base_url` is seeded for display parity with GROK's row but is blanked before
the container mount just the same (never used for auth).

Revision ID: 083_seed_openai_provider
Revises: 082_routing_presets
Create Date: 2026-07-23

Note: chains onto ``082_routing_presets``, a sibling branch's revision that
does not exist in this worktree (this branch was cut before it landed) — the
same expected-failure posture ``081_doctrine_version`` reported for
``080_task_project_budgets``. The local migration-graph AND enum-parity tests
are expected to fail here until this branch integrates alongside 082:
`test_migration_graph_integrity.py` (dangling down_revision, two heads, an
unreachable-root walk) and `test_enum_migration_parity.py` (which shells out
to `alembic upgrade head --sql` and hits the same missing revision id as a
subprocess `KeyError`, not just a static graph-file check). Re-verify the
chain resolves to one head at merge time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "083_seed_openai_provider"
down_revision = "082_routing_presets"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO provider_configs
                (id, name, type, base_url, auth_token_encrypted, enabled, created_at)
            VALUES
                (
                    gen_random_uuid(),
                    'Codex (OpenAI)',
                    'openai',
                    'https://api.openai.com/v1',
                    NULL,
                    true,
                    now()
                )
            ON CONFLICT (name) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Drop model_assignments pointing at the Codex row first to avoid a FK
    # RESTRICT violation on provider_configs.id.
    op.execute(
        sa.text(
            "DELETE FROM model_assignments "
            "WHERE provider_config_id IN ("
            "    SELECT id FROM provider_configs WHERE name = 'Codex (OpenAI)'"
            ")"
        )
    )
    op.execute(sa.text("DELETE FROM provider_configs WHERE name = 'Codex (OpenAI)'"))

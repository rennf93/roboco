"""Flip the Gemini (Google) provider row to enabled=true.

Migration 085 seeded the row `enabled=false`, pending an operator OAuth
setup — but nothing ever flipped it. Unlike Grok (enabled=true only via the
`apply_mode="grok"` write path, which force-enables the row at apply time),
Gemini had no equivalent enable step at all: `apply_mode` grew no "gemini"
case until this same change, so any Mix-mode assignment to a Gemini model
resolved through `resolve_for_agent` against a permanently-disabled row and
silently fell back to the legacy Anthropic path — the provider was wired
end-to-end everywhere except reachable.

Codex (migration 083, `083_seed_openai_provider`) is the closer parity
target: both are subscription-CLI providers with no API key to withhold
behind a disabled row (`~/.codex` / `~/.gemini`, mounted OAuth/subscription
credentials, not a stored token), and Codex seeds `enabled=true` directly for
exactly that reason. This migration brings Gemini to the same state via an
in-place `UPDATE` (the row already exists — no enum touched, no INSERT).

Revision ID: 086_enable_gemini_provider
Revises: 085_seed_gemini_provider
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "086_enable_gemini_provider"
down_revision = "085_seed_gemini_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE provider_configs SET enabled = true WHERE name = 'Gemini (Google)'"
        )
    )


def downgrade() -> None:
    # Honest revert — back to the state migration 085 left it in, not a no-op.
    op.execute(
        sa.text(
            "UPDATE provider_configs SET enabled = false WHERE name = 'Gemini (Google)'"
        )
    )

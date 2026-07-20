"""Add github_app_credentials + projects.github_installation_id.

GitHub App integration (Wave H): a singleton ``github_app_credentials`` row
holds the App id (plain string — a public identifier, not a secret, like
``app_id`` in a GitHub App's own settings page) + the Fernet-encrypted RSA
private key used to mint short-lived installation tokens. A project opts a
repo into App-token auth by recording which installation covers it
(``projects.github_installation_id``, nullable BigInteger — installation ids
are large GitHub-assigned integers). Additive and inert: a null installation
id or an unset App keeps every project on its existing PAT flow
(``ProjectService.get_decrypted_token`` falls back automatically).

Revision ID: 077_github_app
Revises: 076_project_git_provider
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "077_github_app"
down_revision = "076_project_git_provider"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_app_credentials",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("app_id", sa.String(32), nullable=True),
        sa.Column("private_key_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("github_installation_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "github_installation_id")
    op.drop_table("github_app_credentials")

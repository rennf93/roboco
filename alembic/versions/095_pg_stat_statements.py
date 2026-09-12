"""Enable pg_stat_statements for per-query cost/latency telemetry.

Lets an operator answer "what is Postgres actually spending CPU on" without
guessing, e.g. tonight's incident, where nobody could tell that the KB
similarity search was seq-scanning `chunks_journals` without this
extension's per-query stats.

The `postgres` service in all three compose files now sets `command:
["postgres", "-c", "shared_preload_libraries=pg_stat_statements"]`, so the
container restarts once on the next bring-up to actually collect stats; this
migration's `CREATE EXTENSION` alone would otherwise succeed but leave the
view permanently empty. `autocommit_block` (mirroring migration 090's
enum-add pattern) runs it outside the migration's own transaction.

The except is narrowed to `sqlalchemy.exc.DBAPIError` (the wrapper this
asyncpg-backed connection raises) and only swallows two SQLSTATEs, verified
against a live Postgres rather than assumed: `0A000` (feature_not_supported
-- what Postgres actually raises for "extension is not available", i.e. the
control file is missing because the image lacks the contrib package; the
error text mentions a missing file but Postgres deliberately codes this path
0A000, not the file-access class 58P01) and `42501` (insufficient_privilege,
non-superuser role). `0A000` is also what a plain typo of the extension name
raises (Postgres has no way to tell "renamed by mistake" from "genuinely not
installed" -- it just names whatever string was passed), so the guard also
checks the error names `pg_stat_statements` specifically before swallowing;
a typo'd name in this file's own SQL would then re-raise instead of quietly
no-oping forever. Anything else re-raises instead of silently corrupting the
migration run.

Revision ID: 095_pg_stat_statements
Revises: 094_verb_latency_samples
Create Date: 2026-09-05
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy.exc import DBAPIError

revision = "095_pg_stat_statements"
down_revision = "094_verb_latency_samples"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None

logger = logging.getLogger(__name__)

_EXTENSION_NAME = "pg_stat_statements"
_SWALLOWED_SQLSTATES = {"0A000", "42501"}


def _reraise_unless_swallowed(exc: DBAPIError) -> None:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate not in _SWALLOWED_SQLSTATES or _EXTENSION_NAME not in str(exc.orig):
        raise exc
    logger.warning(
        "pg_stat_statements extension op skipped (sqlstate %s): %s",
        sqlstate,
        exc,
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        try:
            op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
        except DBAPIError as exc:
            _reraise_unless_swallowed(exc)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        try:
            op.execute("DROP EXTENSION IF EXISTS pg_stat_statements")
        except DBAPIError as exc:
            _reraise_unless_swallowed(exc)

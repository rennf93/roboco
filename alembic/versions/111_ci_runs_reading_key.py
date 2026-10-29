"""Make the ci_runs reading key treat a NULL workflow as one value.

``roboco/services/ci_history.py`` deduplicates readings with
``ON CONFLICT (project_slug, workflow, observed_at_str) DO NOTHING``, but
108 made that key a plain unique constraint, under which NULLs are
distinct. The ci-watch engine records every reading with
``workflow=None``, so each sweep re-inserted the run it had already
seen, and one red run seen on two sweeps read as two reds, which the
ci_watch_route grader labels a hard regression. The key becomes a unique
index with NULLS NOT DISTINCT (PostgreSQL 15+, like 004's
``ux_model_assignments_scope_key``), once the duplicates it would refuse
are gone: each reading keeps its first observation, the row the conflict
would have kept.

Either shape of the old key is dropped (108's constraint, or the unique
index ``create_all`` builds from the model). ``ci_runs`` is a small
telemetry table (45-day retention) written only by engine sweeps, so the
index builds inside the migration's transaction, as 108's did. A sweep
insert that meets the lock waits for the commit or gives up under the
app's lock_timeout; ``record_runs`` swallows that, and the next sweep
reads the latest run again.

The downgrade restores 108's constraint; the deleted duplicates stay
deleted (each was a copy of a reading that remains).

Revision ID: 111_ci_runs_reading_key
Revises: 110_entire_trails
Create Date: 2026-10-29
"""

from __future__ import annotations

from alembic import op

revision = "111_ci_runs_reading_key"
down_revision = "110_entire_trails"
branch_labels = None
depends_on = None

_KEY = "ux_ci_runs_reading"
_KEY_COLUMNS = ["project_slug", "workflow", "observed_at_str"]


def upgrade() -> None:
    # PARTITION BY groups NULL workflows together, unlike the old key.
    op.execute(
        """
        DELETE FROM ci_runs
        WHERE id IN (
            SELECT id FROM (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY project_slug, workflow, observed_at_str
                        ORDER BY created_at, id
                    ) AS observation
                FROM ci_runs
            ) AS readings
            WHERE observation > 1
        )
        """
    )
    op.execute(f"ALTER TABLE ci_runs DROP CONSTRAINT IF EXISTS {_KEY}")
    op.execute(f"DROP INDEX IF EXISTS {_KEY}")
    op.create_index(
        _KEY,
        "ci_runs",
        _KEY_COLUMNS,
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index(_KEY, table_name="ci_runs")
    op.create_unique_constraint(_KEY, "ci_runs", _KEY_COLUMNS)

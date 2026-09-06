"""Shared fixture-cleanup helper for tests/integration real-DB suites.

Was duplicated verbatim in test_lifecycle_real_db.py and
test_full_lifecycle_real_db.py; test_revision_findings_real_db.py needed
the same thing, so it lives here once instead of a third copy.
"""

from __future__ import annotations

from typing import Any

from roboco.db.tables import (
    AgentTable,
    AuditLogTable,
    ProjectTable,
    TaskTable,
    WorkSessionTable,
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def cleanup_claim_durable_rows(database_url: str, ids: dict[str, Any]) -> None:
    """Delete a fixture's rows on a FRESH connection, whether or not the
    test durably committed them.

    The claim durability boundary (``i_will_work_on`` / ``i_will_plan``)
    commits mid-verb for real, so a test exercising it persists a
    fixture's fixed-slug agents (load-bearing for
    ``agents_config.ESCALATION_CHAIN`` lookups) past ``db_session``'s
    rollback-based teardown; the NEXT test's re-seed with the same slugs
    then hits a UniqueViolation. A test that never committed simply has
    nothing here for this fresh connection to see, so this delete is a
    safe no-op either way. ``task_review_findings`` rows cascade off the
    ``tasks`` delete (``ondelete="CASCADE"``); no separate delete needed.

    Takes plain UUIDs, not the ORM rows: the caller's own
    ``db_session.rollback()`` (run first, to release its locks) expires
    every attribute of those rows, and reading them after that needs an
    async round trip this sync-looking attribute access can't make,
    raising ``MissingGreenlet``. Collecting the ids up front sidesteps that
    entirely.
    """
    engine = create_async_engine(database_url, future=True)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as cleanup:
            task_id = ids["task_id"]
            await cleanup.execute(
                delete(AuditLogTable).where(AuditLogTable.target_id == task_id)
            )
            await cleanup.execute(
                delete(WorkSessionTable).where(WorkSessionTable.task_id == task_id)
            )
            await cleanup.execute(delete(TaskTable).where(TaskTable.id == task_id))
            await cleanup.execute(
                delete(ProjectTable).where(ProjectTable.id == ids["project_id"])
            )
            await cleanup.execute(
                delete(AgentTable).where(AgentTable.id.in_(ids["agent_ids"]))
            )
            await cleanup.commit()
    finally:
        await engine.dispose()

"""Real-DB tests for the vault janitor's TaskService queries.

``list_updated_since`` / ``list_archive_candidates`` / ``sample_stale_tasks``
carry the janitor's resume-marker contract (COALESCE timestamps, ascending
order, half-open archive window) — SQL semantics mocks can't prove. Follows
the ``test_audit_real_query.py`` pattern: real Postgres via the session-scoped
test DB (local: ROBOCO_TEST_DB_PORT=55432 ROBOCO_TEST_DB_USER=renzof).

Foreign rows from other tests may share the DB, so every assertion is scoped
to this module's seeded ids rather than exact result sets.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, TaskTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_agent(session: AsyncSession) -> UUID:
    """``tasks.created_by`` is a NOT NULL FK to ``agents.id``."""
    agent = AgentTable(
        id=uuid4(),
        name="Vault Query Test Agent",
        slug=f"vault-query-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="vault query test",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(agent)
    await session.flush()
    return UUID(str(agent.id))


async def _seed_task(session: AsyncSession, created_by: UUID, **cols: Any) -> UUID:
    """Seed one task; ``cols`` are timestamp/status column overrides."""
    task = TaskTable(
        id=uuid4(),
        title="vault query seed",
        description="seed",
        acceptance_criteria=["seeded"],
        status=cols.pop("status", TaskStatus.IN_PROGRESS),
        priority=2,
        task_type=TaskType.CODE,
        team=Team.BACKEND,
        created_by=created_by,
        **cols,
    )
    session.add(task)
    await session.flush()
    return UUID(str(task.id))


@pytest.mark.asyncio
async def test_changed_set_and_sample_set_are_complementary(
    db_session: AsyncSession,
) -> None:
    """For a given ``since``: touched-after rows appear in the changed set and
    never in the stale sample; touched-before rows the reverse. COALESCE puts
    a never-updated (updated_at NULL) row on its created_at."""
    agent_id = await _seed_agent(db_session)
    now = datetime.now(UTC)
    since = now - timedelta(days=1)
    old_updated = await _seed_task(
        db_session,
        agent_id,
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=3),
    )
    old_never_updated = await _seed_task(
        db_session, agent_id, created_at=now - timedelta(days=3)
    )
    new_updated = await _seed_task(
        db_session,
        agent_id,
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(hours=1),
    )
    new_created = await _seed_task(
        db_session, agent_id, created_at=now - timedelta(hours=1)
    )
    svc = TaskService(db_session)

    changed_ids = {t.id for t in await svc.list_updated_since(since, limit=10_000)}
    stale_ids = {t.id for t in await svc.sample_stale_tasks(since, limit=100_000)}

    assert {new_updated, new_created} <= changed_ids
    assert {old_updated, old_never_updated}.isdisjoint(changed_ids)
    assert {old_updated, old_never_updated} <= stale_ids
    assert {new_updated, new_created}.isdisjoint(stale_ids)
    assert changed_ids.isdisjoint(stale_ids)


@pytest.mark.asyncio
async def test_archive_candidates_window_boundaries(
    db_session: AsyncSession,
) -> None:
    """[after, before): after-inclusive, before-exclusive, terminal-only;
    a terminal row with NULL completed_at falls back to updated_at."""
    agent_id = await _seed_agent(db_session)
    now = datetime.now(UTC)
    after = now - timedelta(days=100)
    before = now - timedelta(days=30)
    created = now - timedelta(days=200)

    at_after = await _seed_task(
        db_session,
        agent_id,
        status=TaskStatus.COMPLETED,
        created_at=created,
        completed_at=after,
    )
    inside = await _seed_task(
        db_session,
        agent_id,
        status=TaskStatus.CANCELLED,
        created_at=created,
        completed_at=now - timedelta(days=60),
    )
    inside_no_completed_at = await _seed_task(
        db_session,
        agent_id,
        status=TaskStatus.COMPLETED,
        created_at=created,
        updated_at=now - timedelta(days=60),
    )
    at_before = await _seed_task(
        db_session,
        agent_id,
        status=TaskStatus.COMPLETED,
        created_at=created,
        completed_at=before,
    )
    non_terminal_inside = await _seed_task(
        db_session,
        agent_id,
        status=TaskStatus.IN_PROGRESS,
        created_at=created,
        completed_at=now - timedelta(days=60),
    )
    svc = TaskService(db_session)

    ids = {t.id for t in await svc.list_archive_candidates(after, before, limit=10_000)}

    assert {at_after, inside, inside_no_completed_at} <= ids
    assert at_before not in ids
    assert non_terminal_inside not in ids


@pytest.mark.asyncio
async def test_list_updated_since_pagination_is_complete_and_ascending(
    db_session: AsyncSession,
) -> None:
    """Paging with a small limit visits every row exactly once, oldest first
    (the capped drain's resume contract)."""
    agent_id = await _seed_agent(db_session)
    base = datetime.now(UTC) + timedelta(days=365)  # beyond any foreign row
    seeded = [
        await _seed_task(
            db_session,
            agent_id,
            created_at=base + timedelta(minutes=i),
        )
        for i in range(5)
    ]
    svc = TaskService(db_session)

    pages: list[UUID] = []
    offset = 0
    while True:
        page = await svc.list_updated_since(base, limit=2, offset=offset)
        if not page:
            break
        pages.extend(UUID(str(t.id)) for t in page)
        offset += len(page)

    assert pages == seeded  # complete, no dupes, ascending touched-order

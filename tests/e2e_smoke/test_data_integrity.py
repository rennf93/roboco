"""Data-integrity smoke scenarios.

C3: deleting a journal entry de-indexes its RAG chunks. The full chain: a
journal entry is indexed (chunks in ``chunks_journals`` + an
``indexed_documents`` tracking row), then ``OptimalService.unindex_journal_entry``
runs against a real pgvector store + the real tracking-row repository, and
both the chunks and the tracking row are gone. This proves the C3 fix's
cross-layer wiring end-to-end (no orphaned chunks bleeding into RAG answers).

Deviations from the brief (noted): the e2e app does not mount the journal
route, ``similar_memory`` queries LEARNINGS + PLAYBOOKS (not JOURNALS), and
the JOURNALS plugin's ``initialize()`` requires the ollama embedder (not
available in the smoke env). So this smoke drives the de-index path directly
with a real ``VectorStore`` against the test DB wrapped in a minimal
``OptimalService`` — exercising the exact pgvector + tracking-row deletes
the production path runs. The ``delete_entry`` call site is covered by
``tests/unit/services/test_journal.py``.

H12: notification dedup must not drop a notification whose recipient set
OVERLAPS but is NOT EQUAL to an existing unacked one's. The ``notify`` do-tool
takes a single recipient and the notifications REST router is not mounted in
the smoke harness, so this smoke drives ``NotificationService._create_notification``
directly against the e2e DB — exercising the real ``_duplicate_unacked_exists``
SQL predicate + real ``NotificationTable`` insert path (the production path).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, IndexedDocumentTable, NotificationTable
from roboco.models import NotificationPriority, NotificationType
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.models.notification import CreateNotificationParams
from roboco.models.optimal import IndexType
from roboco.services.notification import NotificationService
from roboco.services.optimal import OptimalService
from roboco.services.optimal_brain.vector_store import VectorStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from tests.e2e_smoke.harness import E2EStack

_SMOKE_DIM = 4  # tiny embedding dim — the table is created fresh per run
_EXPECTED_H12_ROWS = 2  # first (be-pm) + second (be-pm, main-pm) both persist


def _chunk_dsn(db_url: str) -> str:
    """asyncpg-friendly DSN (postgres:// -> postgresql://)."""
    return db_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres://", "postgresql://", 1
    )


@pytest.mark.asyncio
async def test_c3_deleted_journal_unindexed(e2e_stack: E2EStack) -> None:
    entry_id = uuid4()
    source = f"roboco://journals/{entry_id}"
    source_hash = hashlib.sha256(source.encode()).hexdigest()

    # 1. Provision a real VectorStore for the JOURNALS index against the test
    #    DB. ``initialize`` creates ``chunks_journals`` if absent.
    store = VectorStore(
        dsn=_chunk_dsn(e2e_stack.db_url),
        table_name="chunks_journals",
        vector_dimension=_SMOKE_DIM,
        pool_min_size=1,
        pool_max_size=2,
    )
    await store.initialize()
    try:
        # Seed a chunk row for the journal source with a tiny zero vector.
        pool = store._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO chunks_journals (content, source, embedding, metadata) "
                "VALUES ($1, $2, $3::vector, $4::jsonb)",
                "private reflection that must not leak post-delete",
                source,
                "[" + ",".join(("0.0",) * _SMOKE_DIM) + "]",
                "{}",
            )

        # 2. Seed the tracking row via SQLAlchemy.
        engine = create_async_engine(e2e_stack.db_url, future=True)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                row = IndexedDocumentTable(
                    index_type=IndexType.JOURNALS.value,
                    source=source,
                    source_hash=source_hash,
                    title="Journal: reflect",
                    preview="private reflection that must not leak post-delete",
                    chunk_count=1,
                    extra_data={},
                )
                session.add(row)
                await session.commit()

            # 3. Build a minimal OptimalService whose JOURNALS plugin routes
            #    delete_by_source to the real VectorStore. This mirrors how
            #    unindex_playbook's else-branch calls plugin._require_store.
            svc = OptimalService()
            plugin_obj: Any = type("FakeJournalsPlugin", (), {})()
            object.__setattr__(plugin_obj, "_require_store", store)
            svc._plugins = {IndexType.JOURNALS: plugin_obj}
            svc._initialized = True

            # 4. Run the de-index.
            await svc.unindex_journal_entry(entry_id)

            # 5. Assert the chunk row is gone.
            async with pool.acquire() as conn:
                remaining = await conn.fetchval(
                    "SELECT count(*) FROM chunks_journals WHERE source = $1",
                    source,
                )
            assert remaining == 0, "journal chunk rows survived de-index"

            # 6. Assert the tracking row is gone.
            async with factory() as session:
                rows = (
                    await session.execute(
                        select(IndexedDocumentTable).where(
                            IndexedDocumentTable.source_hash == source_hash,
                            IndexedDocumentTable.index_type == IndexType.JOURNALS.value,
                        )
                    )
                ).all()
            assert rows == [], "indexed_documents tracking row survived de-index"
        finally:
            await engine.dispose()
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# H12 — notification dedup must not drop a notification with an
# overlapping-but-not-equal recipient set (main-pm learns)
# ---------------------------------------------------------------------------


def _seed_sender_agent() -> AgentTable:
    return AgentTable(
        name="cell-pm smoke",
        slug="cell-pm-smoke",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.OFFLINE,
        model_config={},
        system_prompt="",
        capabilities=[],
        permissions={},
        metrics={},
    )


async def _seed_sender(session: Any) -> UUID:
    from sqlalchemy import delete

    # Idempotent: clear any prior smoke sender so the unique slug holds.
    await session.execute(delete(AgentTable).where(AgentTable.slug == "cell-pm-smoke"))
    agent = _seed_sender_agent()
    session.add(agent)
    await session.flush()
    return UUID(str(agent.id))


async def _list_notifications(session: Any, sender_id: UUID) -> list[NotificationTable]:
    result = await session.execute(
        select(NotificationTable)
        .where(NotificationTable.from_agent == sender_id)
        .order_by(NotificationTable.timestamp)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_h12_dedup_does_not_drop_new_recipient(e2e_stack: E2EStack) -> None:
    """H12(a) end-to-end: a blocker sent to {be-pm, main-pm} after an unacked
    one to {be-pm} alone must STILL reach main-pm. Pre-fix the overlap
    predicate dropped the entire second notification for ALL recipients."""
    from roboco.db import base as db_base
    from roboco.db.base import get_db_context

    # Prior smoke tests in the same session (C3) call get_db_context, binding
    # the app's lazy engine to their event loop. pytest-asyncio gives each
    # test its own loop, so reusing that engine raises "attached to a
    # different loop". Drop the holder so this test's first get_db_context
    # binds a fresh engine to THIS loop.
    db_base._DbHolder.engine = None
    db_base._DbHolder.session_factory = None

    # Seed the sender agent through the app's own DB context (same engine the
    # service uses), so the lazy engine binds once and stays alive for the
    # service calls below.
    async with get_db_context() as session:
        sender_id = await _seed_sender(session)
        await session.commit()

    be_pm = uuid4()
    main_pm = uuid4()
    params_first = CreateNotificationParams(
        notification_type=NotificationType.ALERT,
        priority=NotificationPriority.HIGH,
        from_agent="cell-pm-smoke",
        to_agents=[str(be_pm)],
        subject="Blocker on T1",
        body="first blocker — be-pm only",
    )
    params_second = CreateNotificationParams(
        notification_type=NotificationType.ALERT,
        priority=NotificationPriority.HIGH,
        from_agent="cell-pm-smoke",
        to_agents=[str(be_pm), str(main_pm)],
        subject="Blocker on T1",
        body="second blocker — be-pm + main-pm",
    )

    svc = NotificationService()
    await svc._create_notification(params_first)
    await svc._create_notification(params_second)

    async with get_db_context() as session:
        rows = await _list_notifications(session, sender_id)

    assert len(rows) == _EXPECTED_H12_ROWS, (
        f"expected both notifications persisted (overlap≠equal must NOT "
        f"suppress); got {len(rows)} row(s)"
    )
    first, second = rows
    assert set(first.to_agents) == {be_pm}
    assert set(second.to_agents) == {be_pm, main_pm}
    assert main_pm in second.to_agents, "main-pm was dropped by dedup overlap"

"""DB-backed tests for the journals/learnings zero-chunk RAG backfill.

Before the per-index chunk-floor fix, ``ingest()`` returned success with
``chunk_count=0`` for undersized content, so historical journal entries and
learnings were durably recorded in ``journal_entries`` but never landed a row
in the vector store. These tests exercise the REAL SQL against a real
Postgres (`db_session`, see tests/conftest.py) — the JOIN/floor/ANY(...)
logic can't be verified with a mocked session. ``optimal`` itself is mocked
(index_journal_entry / record_learning) since the embedder is out of scope.

Run with: ROBOCO_TEST_DB_PORT=55432 ROBOCO_TEST_DB_USER=renzof pytest ...

``db_session`` rolls back at the end of each test, so nothing THIS file seeds
leaks between its own tests. But the DB itself is session-scoped for the
whole pytest run, and other test files commit real journal/learning rows
against it (e.g. via gateway note-writing tests) — a full-suite / CI run can
start this file's tests with a non-empty ``journal_entries`` table. Every
assertion below is therefore scoped to the specific rows a test creates
(by entry_id or by a uniquified content string), never to a global
processed/remaining count — except the cap test, which measures the
pre-existing "stray" candidate count first and sizes the cap around it.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, JournalEntryTable, JournalTable
from roboco.models.base import AgentRole, AgentStatus, JournalEntryType, Team
from roboco.services import rag_index_failures as rif
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

if TYPE_CHECKING:
    from roboco.models.optimal import IndexJournalEntryParams
    from roboco.services.optimal_brain.indexes.learnings import RecordLearningParams

# Journals floor is 40, learnings floor is 80 (see optimal_brain/indexes/base.py).
_LONG_CONTENT = "x" * 90  # clears both floors
_MID_CONTENT = "y" * 50  # clears JOURNALS (40) but not LEARNINGS (80)
_SHORT_CONTENT = "short"  # clears neither floor

_TEST_CAP = 2  # how many of THIS test's own rows the cap test expects to fit


def _unique_content(base: str) -> str:
    """A floor-clearing content string that can't collide with another
    test's stray committed row, so hash/content-keyed presence checks stay
    deterministic under DB pollution."""
    return f"{base}-{uuid4().hex[:8]}"


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _chunks_tables(_test_database_url: str) -> None:
    """Create minimal chunks_journals/chunks_learnings tables once.

    VectorStore normally provisions these (id, content, source, embedding,
    metadata, tsv); the backfill queries only touch ``source``, so a minimal
    shape is enough. Created via a throwaway engine so the DDL commits
    outside any per-test rolled-back transaction.
    """
    engine = create_async_engine(_test_database_url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS chunks_journals "
                    "(id serial primary key, source text not null)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS chunks_learnings "
                    "(id serial primary key, source text not null)"
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _redirect_db_context(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    """Make get_db_context() (used inside the backfill functions) reuse the
    test's own session/transaction, so uncommitted seed rows are visible
    (read-your-own-writes) without needing an explicit commit + rollback of
    the whole test DB's shared tables between tests."""

    class _Ctx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(rif, "get_db_context", _Ctx)


@pytest_asyncio.fixture
async def _journal(db_session: AsyncSession) -> UUID:
    """Seed one agent + its journal; returns the journal id."""
    agent = AgentTable(
        id=uuid4(),
        name="Backfill Test Agent",
        slug=f"backfill-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    journal = JournalTable(id=uuid4(), agent_id=agent.id)
    db_session.add(journal)
    await db_session.flush()
    return UUID(str(journal.id))


async def _seed_entry(
    db_session: AsyncSession,
    journal_id: UUID,
    content: str,
    *,
    entry_type: JournalEntryType = JournalEntryType.GENERAL,
    is_private: bool = False,
) -> UUID:
    entry = JournalEntryTable(
        id=uuid4(),
        journal_id=journal_id,
        type=entry_type,
        title="t",
        content=content,
        is_private=is_private,
        tags=[],
    )
    db_session.add(entry)
    await db_session.flush()
    return UUID(str(entry.id))


async def _insert_chunk(db_session: AsyncSession, table: str, source: str) -> None:
    await db_session.execute(
        text(f"INSERT INTO {table} (source) VALUES (:s)"), {"s": source}
    )


def _optimal(**overrides: Any) -> MagicMock:
    optimal = MagicMock()
    optimal.is_index_registered = MagicMock(return_value=True)
    optimal.index_journal_entry = AsyncMock()
    optimal.record_learning = AsyncMock()
    for key, value in overrides.items():
        setattr(optimal, key, value)
    return optimal


# ---------------------------------------------------------------------------
# _backfill_journals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_journals_selects_only_zero_chunk_entries(
    db_session: AsyncSession, _journal: UUID
) -> None:
    """An entry with an existing chunks_journals row is left alone; one with
    none is re-ingested. Scoped to these two entry_ids since a polluted DB
    may carry other zero-chunk stray rows the pass also processes."""
    missing_id = await _seed_entry(db_session, _journal, _LONG_CONTENT)
    present_id = await _seed_entry(db_session, _journal, _LONG_CONTENT)
    await _insert_chunk(
        db_session, "chunks_journals", f"roboco://journals/{present_id}"
    )

    optimal = _optimal()
    await rif._backfill_journals(optimal)

    called_ids = {
        c.args[0].entry_id for c in optimal.index_journal_entry.await_args_list
    }
    assert missing_id in called_ids
    assert present_id not in called_ids


@pytest.mark.asyncio
async def test_backfill_journals_excludes_private_entries(
    db_session: AsyncSession, _journal: UUID
) -> None:
    """A private entry is never a candidate — it's deliberately excluded
    from the shared JOURNALS corpus, not a pending backlog item."""
    private_id = await _seed_entry(db_session, _journal, _LONG_CONTENT, is_private=True)

    optimal = _optimal()
    await rif._backfill_journals(optimal)

    called_ids = {
        c.args[0].entry_id for c in optimal.index_journal_entry.await_args_list
    }
    assert private_id not in called_ids


@pytest.mark.asyncio
async def test_backfill_journals_excludes_sub_floor_entries(
    db_session: AsyncSession, _journal: UUID
) -> None:
    """Content still under the JOURNALS floor would zero-chunk again — the
    SELECT excludes it so it is never retried forever."""
    short_id = await _seed_entry(db_session, _journal, _SHORT_CONTENT)

    optimal = _optimal()
    await rif._backfill_journals(optimal)

    called_ids = {
        c.args[0].entry_id for c in optimal.index_journal_entry.await_args_list
    }
    assert short_id not in called_ids


@pytest.mark.asyncio
async def test_backfill_journals_respects_cap(
    db_session: AsyncSession, _journal: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-boot cap bounds how many rows a single pass fetches.

    A polluted DB's stray zero-chunk rows sort first (earliest created_at)
    and spend part of the cap before this test's own rows do. So: measure
    the stray count with an unbounded pass first (mocked — nothing is
    actually written to chunks_journals), then size the cap to exactly
    `strays + _TEST_CAP` and seed one row more than that budget covers.
    Only _TEST_CAP of this test's own rows can then fit.
    """
    monkeypatch.setattr(rif, "_BACKFILL_CAP", 1_000_000)
    strays, _ = await rif._backfill_journals(_optimal())

    own_ids = [
        await _seed_entry(db_session, _journal, _LONG_CONTENT)
        for _ in range(_TEST_CAP + 1)
    ]

    monkeypatch.setattr(rif, "_BACKFILL_CAP", strays + _TEST_CAP)
    optimal = _optimal()
    processed, _remaining = await rif._backfill_journals(optimal)

    assert processed == strays + _TEST_CAP
    called_ids = {
        c.args[0].entry_id for c in optimal.index_journal_entry.await_args_list
    }
    assert len(called_ids & set(own_ids)) == _TEST_CAP


@pytest.mark.asyncio
async def test_backfill_journals_tolerates_failing_row(
    db_session: AsyncSession, _journal: UUID
) -> None:
    """One row's re-index failure never aborts the rest of the pass — the
    fake ingest rejects THIS test's own fail_id specifically (by entry_id),
    so the proof holds regardless of how many stray rows are also in play."""
    fail_id = await _seed_entry(db_session, _journal, _LONG_CONTENT)
    ok_id = await _seed_entry(db_session, _journal, _LONG_CONTENT)
    succeeded: set[UUID] = set()

    async def _index(params: IndexJournalEntryParams) -> None:
        if params.entry_id == fail_id:
            raise RuntimeError("ollama down")
        succeeded.add(params.entry_id)

    optimal = _optimal(index_journal_entry=AsyncMock(side_effect=_index))
    await rif._backfill_journals(optimal)

    called_ids = {
        c.args[0].entry_id for c in optimal.index_journal_entry.await_args_list
    }
    assert fail_id in called_ids
    assert ok_id in succeeded
    assert fail_id not in succeeded


@pytest.mark.asyncio
async def test_backfill_journals_noop_when_index_not_registered(
    db_session: AsyncSession, _journal: UUID
) -> None:
    """A JOURNALS plugin that never initialized is skipped, not errored."""
    await _seed_entry(db_session, _journal, _LONG_CONTENT)
    optimal = _optimal(is_index_registered=MagicMock(return_value=False))

    processed, remaining = await rif._backfill_journals(optimal)

    assert (processed, remaining) == (0, 0)
    optimal.index_journal_entry.assert_not_awaited()


# ---------------------------------------------------------------------------
# _backfill_learnings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_learnings_selects_only_missing_from_chunks_learnings(
    db_session: AsyncSession, _journal: UUID
) -> None:
    """A LEARNING entry already present in chunks_journals (lower floor) can
    still be missing from chunks_learnings (higher floor) — the two passes
    check presence independently. Content is uniquified so this test's own
    row can't be shadowed by a stray row of identical content."""
    content = _unique_content(_LONG_CONTENT)
    entry_id = await _seed_entry(
        db_session, _journal, content, entry_type=JournalEntryType.LEARNING
    )
    # Already indexed into JOURNALS — irrelevant to the LEARNINGS check.
    await _insert_chunk(db_session, "chunks_journals", f"roboco://journals/{entry_id}")

    optimal = _optimal()
    await rif._backfill_learnings(optimal)

    calls_by_content = {
        c.args[0].content: c.args[0] for c in optimal.record_learning.await_args_list
    }
    assert content in calls_by_content
    assert calls_by_content[content].shareable is True


@pytest.mark.asyncio
async def test_backfill_learnings_skips_already_present(
    db_session: AsyncSession, _journal: UUID
) -> None:
    """A learning whose hashed source already has a chunks_learnings row is
    left alone."""
    content = _unique_content(_LONG_CONTENT)
    await _seed_entry(
        db_session, _journal, content, entry_type=JournalEntryType.LEARNING
    )
    await _insert_chunk(db_session, "chunks_learnings", rif._learning_source(content))

    optimal = _optimal()
    await rif._backfill_learnings(optimal)

    called_contents = {
        c.args[0].content for c in optimal.record_learning.await_args_list
    }
    assert content not in called_contents


@pytest.mark.asyncio
async def test_backfill_learnings_excludes_sub_floor_entries(
    db_session: AsyncSession, _journal: UUID
) -> None:
    """Content between the JOURNALS floor and the (higher) LEARNINGS floor
    would zero-chunk again in LEARNINGS — excluded so it's never retried
    forever. The length-based SQL filter excludes it structurally, so this
    holds regardless of any other candidate rows in play."""
    await _seed_entry(
        db_session, _journal, _MID_CONTENT, entry_type=JournalEntryType.LEARNING
    )

    optimal = _optimal()
    await rif._backfill_learnings(optimal)

    called_contents = {
        c.args[0].content for c in optimal.record_learning.await_args_list
    }
    assert _MID_CONTENT not in called_contents


@pytest.mark.asyncio
async def test_backfill_learnings_shareable_reflects_privacy(
    db_session: AsyncSession, _journal: UUID
) -> None:
    """A private learning is still recorded, just non-shareable — mirrors
    the live _schedule_rag_index path."""
    content = _unique_content(_LONG_CONTENT)
    await _seed_entry(
        db_session,
        _journal,
        content,
        entry_type=JournalEntryType.LEARNING,
        is_private=True,
    )

    optimal = _optimal()
    await rif._backfill_learnings(optimal)

    calls_by_content = {
        c.args[0].content: c.args[0] for c in optimal.record_learning.await_args_list
    }
    assert content in calls_by_content
    assert calls_by_content[content].shareable is False


@pytest.mark.asyncio
async def test_backfill_learnings_tolerates_failing_row(
    db_session: AsyncSession, _journal: UUID
) -> None:
    """One row's re-index failure never aborts the rest of the pass — the
    fake ingest rejects THIS test's own fail_content specifically (by
    content), so the proof holds regardless of stray rows also in play."""
    fail_content = _unique_content(f"{_LONG_CONTENT}-fail")
    ok_content = _unique_content(f"{_LONG_CONTENT}-ok")
    await _seed_entry(
        db_session, _journal, fail_content, entry_type=JournalEntryType.LEARNING
    )
    await _seed_entry(
        db_session, _journal, ok_content, entry_type=JournalEntryType.LEARNING
    )
    succeeded: set[str] = set()

    async def _record(params: RecordLearningParams) -> None:
        if params.content == fail_content:
            raise RuntimeError("ollama down")
        succeeded.add(params.content)

    optimal = _optimal(record_learning=AsyncMock(side_effect=_record))
    await rif._backfill_learnings(optimal)

    called_contents = {
        c.args[0].content for c in optimal.record_learning.await_args_list
    }
    assert fail_content in called_contents
    assert ok_content in succeeded
    assert fail_content not in succeeded


@pytest.mark.asyncio
async def test_backfill_learnings_noop_when_index_not_registered(
    db_session: AsyncSession, _journal: UUID
) -> None:
    """A LEARNINGS plugin that never initialized is skipped, not errored."""
    await _seed_entry(
        db_session, _journal, _LONG_CONTENT, entry_type=JournalEntryType.LEARNING
    )
    optimal = _optimal(is_index_registered=MagicMock(return_value=False))

    processed, remaining = await rif._backfill_learnings(optimal)

    assert (processed, remaining) == (0, 0)
    optimal.record_learning.assert_not_awaited()


# ---------------------------------------------------------------------------
# _learning_source — must match LearningsIndexPlugin.record_learning exactly,
# or the presence check can never find what the live path indexed.
# ---------------------------------------------------------------------------


def test_learning_source_matches_plugin_hash() -> None:
    content = "a distilled lesson"
    expected_hash = hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()[
        :16
    ]
    assert rif._learning_source(content) == f"roboco://learnings/lrn-{expected_hash}"


# ---------------------------------------------------------------------------
# backfill_unindexed_journals — orchestration + isolation between passes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_unindexed_journals_isolates_pass_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hard failure in one pass (e.g. lost DB connection) doesn't prevent
    the other pass from running."""
    monkeypatch.setattr(
        rif, "_backfill_journals", AsyncMock(side_effect=RuntimeError("db down"))
    )
    learnings_mock = AsyncMock(return_value=(3, 1))
    monkeypatch.setattr(rif, "_backfill_learnings", learnings_mock)

    result = await rif.backfill_unindexed_journals(MagicMock())

    assert result == {
        "journals_processed": 0,
        "journals_remaining": 0,
        "learnings_processed": 3,
        "learnings_remaining": 1,
    }
    learnings_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_backfill_unindexed_journals_returns_combined_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: both passes run and their counts are combined."""
    monkeypatch.setattr(rif, "_backfill_journals", AsyncMock(return_value=(2, 0)))
    monkeypatch.setattr(rif, "_backfill_learnings", AsyncMock(return_value=(1, 0)))

    result = await rif.backfill_unindexed_journals(MagicMock())

    assert result == {
        "journals_processed": 2,
        "journals_remaining": 0,
        "learnings_processed": 1,
        "learnings_remaining": 0,
    }

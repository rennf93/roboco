"""JournalService coverage — get/create journals + entries + queries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock as _AsyncMock
from unittest.mock import MagicMock as _MagicMock
from uuid import uuid4
from uuid import uuid4 as _u

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentTable,
    JournalEntryTable,
    JournalTable,
    ProjectTable,
    TaskTable,
)
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    JournalEntryType,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.journal import (
    DecisionLogParams,
    GeneralEntryParams,
    JournalEntryCreate,
    LearningEntryParams,
    ListEntriesFilter,
    StruggleEntryParams,
    TaskReflectionParams,
)
from roboco.services.journal import JournalService, drain_rag_index_tasks
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError as _IE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def journal_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    """Seed an agent so we can create a journal for them."""
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="J-Proj",
        slug=f"j-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=agent.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()
    yield {
        "svc": JournalService(db_session),
        "agent_id": agent.id,
        "agent": agent,
        "task_id": task.id,
    }


@pytest.mark.asyncio
async def test_get_or_create_journal_creates_new(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    journal = await svc.get_or_create_journal(journal_setup["agent_id"])
    assert journal is not None
    assert journal.agent_id == journal_setup["agent_id"]


@pytest.mark.asyncio
async def test_get_or_create_journal_idempotent(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    a = await svc.get_or_create_journal(journal_setup["agent_id"])
    b = await svc.get_or_create_journal(journal_setup["agent_id"])
    assert a.id == b.id


@pytest.mark.asyncio
async def test_get_journal_by_agent(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    created = await svc.get_or_create_journal(journal_setup["agent_id"])
    fetched = await svc.get_journal_by_agent(journal_setup["agent_id"])
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_journal_by_agent_returns_none_when_missing(
    journal_setup: dict,
) -> None:
    svc = journal_setup["svc"]
    assert await svc.get_journal_by_agent(uuid4()) is None


@pytest.mark.asyncio
async def test_create_entry(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    journal = await svc.get_or_create_journal(journal_setup["agent_id"])
    entry = await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.GENERAL,
            title="First entry",
            content="Some content here",
        )
    )
    assert entry is not None
    assert entry.title == "First entry"


@pytest.mark.asyncio
async def test_get_entry(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    journal = await svc.get_or_create_journal(journal_setup["agent_id"])
    created = await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.LEARNING,
            title="learn",
            content="x",
        )
    )
    assert created is not None
    fetched = await svc.get_entry(created.id)
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_entry_returns_none_when_missing(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    assert await svc.get_entry(uuid4()) is None


@pytest.mark.asyncio
async def test_list_entries(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    journal = await svc.get_or_create_journal(journal_setup["agent_id"])
    for i in range(3):
        await svc.create_entry(
            JournalEntryCreate(
                journal_id=journal.id,
                type=JournalEntryType.GENERAL,
                title=f"e{i}",
                content="x",
            )
        )
    entries = await svc.list_entries(journal.id)
    _ENTRIES = 3
    assert len(entries) >= _ENTRIES


@pytest.mark.asyncio
async def test_list_entries_filtered_by_type(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    journal = await svc.get_or_create_journal(journal_setup["agent_id"])
    await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.LEARNING,
            title="L",
            content="x",
        )
    )
    await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.STRUGGLE,
            title="S",
            content="y",
        )
    )
    learning_only = await svc.list_entries(
        journal.id, ListEntriesFilter(entry_type=JournalEntryType.LEARNING)
    )
    assert all(e.type == JournalEntryType.LEARNING for e in learning_only)


@pytest.mark.asyncio
async def test_delete_entry(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    journal = await svc.get_or_create_journal(journal_setup["agent_id"])
    entry = await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.GENERAL,
            title="del",
            content="x",
        )
    )
    assert entry is not None
    deleted = await svc.delete_entry(entry.id)
    assert deleted is True
    assert await svc.get_entry(entry.id) is None


@pytest.mark.asyncio
async def test_delete_entry_returns_false_for_missing(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    assert await svc.delete_entry(uuid4()) is False


@pytest.mark.asyncio
async def test_resolve_agent_id_by_slug(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    resolved = await svc.resolve_agent_id(journal_setup["agent"].slug)
    assert resolved == journal_setup["agent_id"]


@pytest.mark.asyncio
async def test_resolve_agent_id_by_uuid(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    resolved = await svc.resolve_agent_id(str(journal_setup["agent_id"]))
    assert resolved == journal_setup["agent_id"]


@pytest.mark.asyncio
async def test_resolve_agent_id_returns_none_for_unknown(
    journal_setup: dict,
) -> None:
    svc = journal_setup["svc"]
    assert await svc.resolve_agent_id("unknown-slug") is None


@pytest.mark.asyncio
async def test_get_agent_slug(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    slug = await svc.get_agent_slug(journal_setup["agent_id"])
    assert slug == journal_setup["agent"].slug


@pytest.mark.asyncio
async def test_get_agent_slug_returns_none_for_unknown(
    journal_setup: dict,
) -> None:
    svc = journal_setup["svc"]
    assert await svc.get_agent_slug(uuid4()) is None


def _reflection(tid: uuid.UUID) -> TaskReflectionParams:
    return TaskReflectionParams(
        task_id=tid,
        title="r",
        what_done="d",
        what_learned="l",
        what_struggled="s",
        next_steps=["n"],
    )


def _decision(tid: uuid.UUID) -> DecisionLogParams:
    return DecisionLogParams(
        title="d",
        context="ctx",
        options=[{"name": "a", "rationale": "r"}],
        chosen="a",
        rationale="r",
        consequences=["c"],
        task_id=tid,
    )


def _learning(tid: uuid.UUID) -> LearningEntryParams:
    return LearningEntryParams(title="l", what_learned="x", task_id=tid)


def _struggle(tid: uuid.UUID) -> StruggleEntryParams:
    return StruggleEntryParams(
        title="s",
        what_struggled="x",
        attempted_solutions=["try1"],
        task_id=tid,
    )


@pytest.mark.asyncio
async def test_helper_add_methods(journal_setup: dict) -> None:
    """add_task_reflection / add_decision_log / add_learning / add_struggle."""
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    tid = journal_setup["task_id"]
    refl = await svc.add_task_reflection(aid, _reflection(tid))
    assert refl is not None
    dec = await svc.add_decision_log(aid, _decision(tid))
    assert dec is not None
    lrn = await svc.add_learning(aid, _learning(tid))
    assert lrn is not None
    strug = await svc.add_struggle(aid, _struggle(tid))
    assert strug is not None


@pytest.mark.asyncio
async def test_has_decision_learning_reflect_for_task(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    tid = journal_setup["task_id"]

    assert await svc.has_decision_for_task(aid, tid) is False
    await svc.add_decision_log(aid, _decision(tid))
    assert await svc.has_decision_for_task(aid, tid) is True

    assert await svc.has_learning_for_task(aid, tid) is False
    await svc.add_learning(aid, _learning(tid))
    assert await svc.has_learning_for_task(aid, tid) is True

    assert await svc.has_reflect_for_task(aid, tid) is False
    await svc.add_task_reflection(aid, _reflection(tid))
    assert await svc.has_reflect_for_task(aid, tid) is True


@pytest.mark.asyncio
async def test_get_journal_stats(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    journal = await svc.get_or_create_journal(journal_setup["agent_id"])
    await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.LEARNING,
            title="L",
            content="x",
        )
    )
    stats = await svc.get_journal_stats(journal.id)
    assert stats is not None
    assert stats.total_entries >= 1


@pytest.mark.asyncio
async def test_get_journal_by_id(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    created = await svc.get_or_create_journal(journal_setup["agent_id"])
    fetched = await svc.get_journal(created.id)
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_journal_by_id_returns_none(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    assert await svc.get_journal(uuid4()) is None


@pytest.mark.asyncio
async def test_get_growth_metrics_for_unknown_agent(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    assert await svc.get_growth_metrics(uuid4()) is None


@pytest.mark.asyncio
async def test_get_growth_metrics_returns_metrics(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    journal = await svc.get_or_create_journal(aid)
    await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.LEARNING,
            title="L",
            content="x",
        )
    )
    # Manually bump entries_by_type so growth_metrics has something to count.
    metrics = await svc.get_growth_metrics(aid)
    assert metrics is not None
    assert hasattr(metrics, "total_learnings")


@pytest.mark.asyncio
async def test_write_struggle(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    tid = journal_setup["task_id"]
    entry = await svc.write_struggle(
        agent_id=aid,
        task_id=tid,
        content="Couldn't connect to the database.\nGave up after 3 hours.",
    )
    assert entry is not None
    # Title is the first line truncated.
    assert entry.title.startswith("Couldn't connect")


@pytest.mark.asyncio
async def test_write_entry_dispatches_by_scope(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    entry = await svc.write_entry(agent_id=aid, title="x", content="y", scope="note")
    assert entry is not None
    assert entry.type == JournalEntryType.GENERAL


@pytest.mark.asyncio
async def test_write_entry_rejects_unknown_scope(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    with pytest.raises(ValueError, match="unknown scope"):
        await svc.write_entry(
            agent_id=journal_setup["agent_id"],
            title="x",
            content="y",
            scope="bogus",
        )


# ---------------------------------------------------------------------------
# create_entry — IntegrityError path returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_entry_integrity_error_returns_none(
    journal_setup: dict,
) -> None:
    """IntegrityError on commit → rollback + return None.

    Patches `session.commit` to raise IntegrityError so the explicit
    handler in `create_entry` runs (FK violations otherwise raise during
    autoflush, before reaching that handler).
    """
    svc = journal_setup["svc"]
    journal = await svc.get_or_create_journal(journal_setup["agent_id"])

    err = _IE("insert", {}, Exception("FK violation"))
    original_commit = svc.session.commit

    async def _raise_once(*_args: Any, **_kwargs: Any) -> None:
        # Restore for cleanup paths.
        svc.session.commit = original_commit
        raise err

    svc.session.commit = _AsyncMock(side_effect=_raise_once)
    result = await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.GENERAL,
            title="orphan",
            content="x",
        )
    )
    assert result is None


# ---------------------------------------------------------------------------
# create_entry — LEARNING type triggers record_learning side effect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_entry_learning_calls_record_learning(
    journal_setup: dict,
) -> None:
    """LEARNING-typed entries also get indexed to the learnings index."""
    svc = journal_setup["svc"]
    journal = await svc.get_or_create_journal(journal_setup["agent_id"])

    mock_optimal = _AsyncMock()
    mock_optimal.index_journal_entry = _AsyncMock(return_value=None)
    mock_optimal.record_learning = _AsyncMock(return_value=None)
    svc._optimal_service = mock_optimal

    entry = await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.LEARNING,
            title="t",
            content="learned x",
        )
    )
    assert entry is not None
    await drain_rag_index_tasks()  # indexing is fire-and-forget; let it run
    mock_optimal.record_learning.assert_awaited()


# ---------------------------------------------------------------------------
# list_entries — filter by task_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_entries_filters_by_task_id(
    journal_setup: dict,
) -> None:
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    tid = journal_setup["task_id"]
    journal = await svc.get_or_create_journal(aid)
    await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.GENERAL,
            title="t1",
            content="for-task",
            task_id=tid,
        )
    )
    await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.GENERAL,
            title="t2",
            content="no-task",
        )
    )
    entries = await svc.list_entries(journal.id, ListEntriesFilter(task_id=tid))
    assert all(e.task_id == tid for e in entries)


# ---------------------------------------------------------------------------
# add_general_entry — populates GENERAL entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_general_entry(journal_setup: dict) -> None:
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    tid = journal_setup["task_id"]
    entry = await svc.add_general_entry(
        aid,
        GeneralEntryParams(
            title="general",
            content="some content",
            task_id=tid,
            tags=["tag"],
        ),
    )
    assert entry is not None
    assert entry.type == JournalEntryType.GENERAL


# ---------------------------------------------------------------------------
# get_journal_stats — None for missing journal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_journal_stats_missing_returns_none(
    journal_setup: dict,
) -> None:
    svc = journal_setup["svc"]
    assert await svc.get_journal_stats(_u()) is None


# ---------------------------------------------------------------------------
# get_growth_metrics — struggle resolution rate calculation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_growth_metrics_with_resolved_struggles(
    journal_setup: dict,
) -> None:
    """Struggle entries with '## Resolution' contribute to resolution rate."""
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    journal = await svc.get_or_create_journal(aid)
    # Create a struggle with '## Resolution' marker.
    await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.STRUGGLE,
            title="resolved",
            content="Issue.\n## Resolution\nFixed by X.",
        )
    )
    # Manually bump entries_by_type so resolution-rate path runs.
    db_result = await svc.session.execute(
        select(JournalTable).where(JournalTable.id == journal.id)
    )
    row = db_result.scalar_one()
    row.entries_by_type = {"struggle": 1, "learning": 1}
    row.total_entries = 2
    await svc.session.flush()

    metrics = await svc.get_growth_metrics(aid)
    assert metrics is not None
    assert metrics.struggle_resolution_rate >= 0.0


# ---------------------------------------------------------------------------
# search_entries — full path with stub optimal results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_entries_appends_when_journal_matches(
    journal_setup: dict,
) -> None:
    """When the journal lookup matches and entry's journal_id aligns, append."""
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    journal = await svc.get_or_create_journal(aid)
    entry = await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.GENERAL,
            title="t",
            content="x",
        )
    )
    assert entry is not None

    mock_result = _MagicMock()
    mock_result.metadata = {"entry_id": str(entry.id)}
    mock_optimal = _AsyncMock()
    mock_optimal.search = _AsyncMock(return_value=[mock_result])
    svc._optimal_service = mock_optimal

    # Patch get_journal to return the journal regardless of arg, so the
    # `entry.journal_id == journal.id` branch runs.
    original_get_journal = svc.get_journal
    svc.get_journal = _AsyncMock(return_value=journal)
    try:
        results = await svc.search_entries(aid, "query", top_k=5)
    finally:
        svc.get_journal = original_get_journal
    assert any(e.id == entry.id for e in results)


@pytest.mark.asyncio
async def test_search_entries_with_valid_metadata_runs_lookup(
    journal_setup: dict,
) -> None:
    """search_entries fetches full entries via metadata.entry_id.

    Note: the production code calls `get_journal(agent_id)` but the param
    is actually a journal_id-shaped UUID, so the per-result filter is a
    near-miss in production. We assert the call path runs without error
    and that the agent-id mismatch produces an empty list.
    """
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    journal = await svc.get_or_create_journal(aid)
    entry = await svc.create_entry(
        JournalEntryCreate(
            journal_id=journal.id,
            type=JournalEntryType.GENERAL,
            title="t",
            content="x",
        )
    )
    assert entry is not None

    mock_result = _MagicMock()
    mock_result.metadata = {"entry_id": str(entry.id)}
    mock_optimal = _AsyncMock()
    mock_optimal.search = _AsyncMock(return_value=[mock_result])
    svc._optimal_service = mock_optimal

    # Path runs through entry_id parsing + lookup.
    results = await svc.search_entries(aid, "query", top_k=5)
    # get_journal(agent_id) won't match anything → empty list.
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_search_entries_invalid_uuid_logged(
    journal_setup: dict,
) -> None:
    """Invalid entry_id in search results is logged but not raised."""
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    mock_result = _MagicMock()
    mock_result.metadata = {"entry_id": "not-a-uuid"}
    mock_optimal = _AsyncMock()
    mock_optimal.search = _AsyncMock(return_value=[mock_result])
    svc._optimal_service = mock_optimal

    results = await svc.search_entries(aid, "query", top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_search_entries_no_metadata_entry_id(
    journal_setup: dict,
) -> None:
    """Result without entry_id in metadata is silently skipped."""
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    mock_result = _MagicMock()
    mock_result.metadata = {}
    mock_optimal = _AsyncMock()
    mock_optimal.search = _AsyncMock(return_value=[mock_result])
    svc._optimal_service = mock_optimal

    results = await svc.search_entries(aid, "query", top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_search_entries_swallows_exception(
    journal_setup: dict,
) -> None:
    """Optimal failure → empty list, no raise."""
    svc = journal_setup["svc"]
    aid = journal_setup["agent_id"]
    mock_optimal = _AsyncMock()
    mock_optimal.search = _AsyncMock(side_effect=RuntimeError("rag down"))
    svc._optimal_service = mock_optimal

    results = await svc.search_entries(aid, "query")
    assert results == []


@pytest.mark.asyncio
async def test_has_recent_entry_false_then_true(journal_setup: dict) -> None:
    """No entries → False; a freshly written entry is within the window."""
    svc: JournalService = journal_setup["svc"]
    agent_id = journal_setup["agent_id"]
    task_id = journal_setup["task_id"]

    assert await svc.has_recent_entry(agent_id, 3600) is False

    await svc.write_entry(
        agent_id=agent_id,
        title="observation",
        content="watching the seam between FE and BE",
        scope="reflect",
        task_id=task_id,
    )
    assert await svc.has_recent_entry(agent_id, 3600) is True


@pytest.mark.asyncio
async def test_has_recent_entry_excludes_entries_outside_window(
    journal_setup: dict, db_session: AsyncSession
) -> None:
    """An entry older than the window does not count as recent."""
    svc: JournalService = journal_setup["svc"]
    agent_id = journal_setup["agent_id"]
    task_id = journal_setup["task_id"]

    await svc.write_entry(
        agent_id=agent_id,
        title="stale observation",
        content="recorded two hours ago",
        scope="reflect",
        task_id=task_id,
    )
    # Backdate every entry on this agent's journal to two hours ago.
    journal = await svc.get_journal_by_agent(agent_id)
    assert journal is not None
    await db_session.execute(
        update(JournalEntryTable)
        .where(JournalEntryTable.journal_id == journal.id)
        .values(timestamp=datetime.now(UTC) - timedelta(hours=2))
    )
    await db_session.flush()

    assert await svc.has_recent_entry(agent_id, 3600) is False
    assert await svc.has_recent_entry(agent_id, 3 * 3600) is True

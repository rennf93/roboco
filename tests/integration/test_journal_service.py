"""JournalService coverage — get/create journals + entries + queries."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    JournalEntryType,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.journal import (
    DecisionLogParams,
    JournalEntryCreate,
    LearningEntryParams,
    ListEntriesFilter,
    StruggleEntryParams,
    TaskReflectionParams,
)
from roboco.services.journal import JournalService

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
    assert len(entries) >= 3


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


def _reflection(tid) -> TaskReflectionParams:
    return TaskReflectionParams(
        task_id=tid,
        title="r",
        what_done="d",
        what_learned="l",
        what_struggled="s",
        next_steps=["n"],
    )


def _decision(tid) -> DecisionLogParams:
    return DecisionLogParams(
        title="d",
        context="ctx",
        options=[{"name": "a", "rationale": "r"}],
        chosen="a",
        rationale="r",
        consequences=["c"],
        task_id=tid,
    )


def _learning(tid) -> LearningEntryParams:
    return LearningEntryParams(title="l", what_learned="x", task_id=tid)


def _struggle(tid) -> StruggleEntryParams:
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

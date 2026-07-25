"""Librarian engine: originate ONE held mining cycle, deduped, never authors
content itself, and — like SentinelEngine — needs no per-project opt-in to
RUN (org-scoped: it mines journals/learnings org-wide, not a repo).

Mirrors test_sentinel_engine.py: like sentinel, the mining task's
``project_id`` still resolves against the RoboCo project (a hard
TaskService._require_target_or_umbrella invariant every non-coordination
task carries) even though the program itself needs no per-project opt-in.
Also covers ``mining_context``'s two sections and
``existing_playbook_titles_lower``'s live dedup surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.config import settings as cfg
from roboco.db.tables import (
    AgentTable,
    BoardProgramCycleTable,
    JournalEntryTable,
    JournalTable,
    PlaybookTable,
    ProjectTable,
    SystemSettingTable,
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    JournalEntryType,
    PlaybookStatus,
    Team,
)
from roboco.models.base import TaskStatus as TS
from roboco.services.librarian_engine import LibrarianEngine
from roboco.services.task import (
    LIBRARIAN_SOURCE,
    PERISCOPE_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    SENTINEL_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    get_task_service,
)
from sqlalchemy import delete, select, update

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
AUDITOR_UUID = _foundation.AGENTS["auditor"].uuid
SLUG = "roboco"
ONE = 1


@pytest_asyncio.fixture(autouse=True)
async def _purge_board_program_pollution(db_session: AsyncSession) -> None:
    """See test_board_program_engine.py's identical fixture: Board Program
    settings-store rows / ledger rows / open exploration tasks are shared,
    cross-test-persistent DB state. Purge before every test in this file —
    also sweeps any playbook this file's own tests drafted."""
    await db_session.execute(
        delete(SystemSettingTable).where(SystemSettingTable.key.like("board_program.%"))
    )
    await db_session.execute(delete(BoardProgramCycleTable))
    await db_session.execute(
        update(TaskTable)
        .where(
            TaskTable.source.in_(
                [
                    ROADMAP_SOURCE,
                    X_FEATURE_EXPLORATION_SOURCE,
                    PEST_CONTROL_SOURCE,
                    PERISCOPE_SOURCE,
                    SENTINEL_SOURCE,
                    LIBRARIAN_SOURCE,
                ]
            ),
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    # PlaybookTable.tags is a plain JSON column (no portable "contains"
    # operator) — filter by this file's own seeded creator instead.
    await db_session.execute(
        delete(PlaybookTable).where(PlaybookTable.created_by == AUDITOR_UUID)
    )
    await db_session.commit()


async def _seed(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (AUDITOR_UUID, "auditor", AgentRole.AUDITOR, None),
    ):
        if await session.get(AgentTable, uuid) is None:
            session.add(
                AgentTable(
                    id=uuid,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=team,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt="x",
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )
    await session.flush()
    session.add(
        ProjectTable(
            name="RoboCo",
            slug=SLUG,
            git_url="https://github.com/x/roboco.git",
            default_branch="master",
            protected_branches=["master"],
            assigned_cell=Team.BACKEND,
            created_by=SYSTEM_UUID,
            is_active=True,
        )
    )
    await session.flush()


def _arm(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    session.add(SystemSettingTable(key="board_program.librarian.enabled", value="true"))
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)


async def _auditor_journal(session: AsyncSession) -> JournalTable:
    """One JournalTable row per agent (unique constraint on ``agent_id``) —
    reused across multiple ``_add_learning`` calls in the same test instead
    of re-inserting and hitting ``uq_journals_agent_id``."""
    existing = (
        await session.execute(
            select(JournalTable).where(JournalTable.agent_id == AUDITOR_UUID)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    journal = JournalTable(id=uuid4(), agent_id=AUDITOR_UUID)
    session.add(journal)
    await session.flush()
    return journal


async def _add_learning(
    session: AsyncSession, *, title: str, is_private: bool = False
) -> None:
    journal = await _auditor_journal(session)
    session.add(
        JournalEntryTable(
            id=uuid4(),
            journal_id=journal.id,
            type=JournalEntryType.LEARNING,
            title=title,
            content="content",
            is_private=is_private,
        )
    )
    await session.flush()


# --------------------------------------------------------------------------- #
# run_cycle
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_disabled_creates_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    engine = LibrarianEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_librarian_cycles() == []


@pytest.mark.asyncio
async def test_no_legacy_flag_backdoor(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No settings-store row at all = disabled — there is no legacy env flag
    for librarian to fall back to (unlike roadmap/x_feature)."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    engine = LibrarianEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_enabled_originates_held_mining_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    engine = LibrarianEngine(db_session)
    task = await engine.run_cycle()
    assert task is not None

    open_cycles = await get_task_service(db_session).list_open_librarian_cycles()
    assert len(open_cycles) == ONE
    cycle = open_cycles[0]
    assert cycle.status == TS.PENDING
    assert cycle.confirmed_by_human is False  # HELD; board-dispatched only
    assert cycle.assigned_to == AUDITOR_UUID
    assert cycle.team == Team.BOARD
    assert cycle.source == LIBRARIAN_SOURCE
    assert "Librarian" in cycle.title


@pytest.mark.asyncio
async def test_dedupe_one_open_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    await LibrarianEngine(db_session).run_cycle()
    second = await LibrarianEngine(db_session).run_cycle()
    assert second is None
    assert len(await get_task_service(db_session).list_open_librarian_cycles()) == ONE


@pytest.mark.asyncio
async def test_settings_store_false_creates_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    db_session.add(
        SystemSettingTable(key="board_program.librarian.enabled", value="false")
    )
    await db_session.flush()
    engine = LibrarianEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_unresolvable_project_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    db_session.add(
        SystemSettingTable(key="board_program.librarian.enabled", value="true")
    )
    monkeypatch.setattr(cfg, "self_heal_project_slug", "no-such-project")
    await db_session.flush()
    engine = LibrarianEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_librarian_cycles() == []


@pytest.mark.asyncio
async def test_a_completed_cycle_unblocks_the_next_one(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    first = await LibrarianEngine(db_session).run_cycle()
    assert first is not None
    first.status = TS.COMPLETED
    await db_session.flush()

    second = await LibrarianEngine(db_session).run_cycle()
    assert second is not None
    assert second.id != first.id


# --------------------------------------------------------------------------- #
# mining_context
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mining_context_empty_when_nothing_to_report(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(JournalEntryTable))
    await db_session.execute(delete(PlaybookTable))
    await db_session.commit()
    context = await LibrarianEngine(db_session).mining_context()
    assert context == ""


@pytest.mark.asyncio
async def test_mining_context_renders_recurring_learning_topics(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(JournalEntryTable))
    await db_session.commit()
    await _seed(db_session)
    await _add_learning(db_session, title="venv rot after gate skip")
    await _add_learning(db_session, title="venv rot after gate skip")

    context = await LibrarianEngine(db_session).mining_context()
    assert "Recurring learning topics" in context
    assert "venv rot after gate skip" in context
    assert "recurred 2x" in context


@pytest.mark.asyncio
async def test_mining_context_falls_back_to_sample_when_nothing_recurs(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(JournalEntryTable))
    await db_session.commit()
    await _seed(db_session)
    await _add_learning(db_session, title="a one-off learning")

    context = await LibrarianEngine(db_session).mining_context()
    assert "Recurring learning topics" in context
    assert "a one-off learning" in context
    assert "recurred" not in context


@pytest.mark.asyncio
async def test_mining_context_excludes_private_learnings(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(JournalEntryTable))
    await db_session.commit()
    await _seed(db_session)
    await _add_learning(db_session, title="a private reflection", is_private=True)

    context = await LibrarianEngine(db_session).mining_context()
    assert "a private reflection" not in context


@pytest.mark.asyncio
async def test_mining_context_renders_existing_playbook_titles(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(PlaybookTable))
    await db_session.commit()
    await _seed(db_session)
    db_session.add(
        PlaybookTable(
            id=uuid4(),
            title="Verify venv freshness before gate",
            slug="verify-venv-freshness-before-gate",
            problem="p",
            procedure="pr",
            tags=[],
            status=PlaybookStatus.DRAFT.value,
            created_by=AUDITOR_UUID,
        )
    )
    await db_session.flush()

    context = await LibrarianEngine(db_session).mining_context()
    assert "Existing playbook titles" in context
    assert "Verify venv freshness before gate" in context


@pytest.mark.asyncio
async def test_mining_context_excludes_archived_playbooks(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(PlaybookTable))
    await db_session.commit()
    await _seed(db_session)
    db_session.add(
        PlaybookTable(
            id=uuid4(),
            title="An archived one",
            slug="an-archived-one",
            problem="p",
            procedure="pr",
            tags=[],
            status=PlaybookStatus.ARCHIVED.value,
            created_by=AUDITOR_UUID,
        )
    )
    await db_session.flush()

    context = await LibrarianEngine(db_session).mining_context()
    assert "An archived one" not in context


# --------------------------------------------------------------------------- #
# existing_playbook_titles_lower
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_existing_playbook_titles_lower_is_lowercased_and_live(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(PlaybookTable))
    await db_session.commit()
    await _seed(db_session)
    db_session.add(
        PlaybookTable(
            id=uuid4(),
            title="Mixed CASE Title",
            slug="mixed-case-title",
            problem="p",
            procedure="pr",
            tags=[],
            status=PlaybookStatus.DRAFT.value,
            created_by=AUDITOR_UUID,
        )
    )
    await db_session.flush()

    titles = await LibrarianEngine(db_session).existing_playbook_titles_lower()
    assert "mixed case title" in titles


@pytest.mark.asyncio
async def test_existing_playbook_titles_lower_excludes_archived(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(PlaybookTable))
    await db_session.commit()
    await _seed(db_session)
    db_session.add(
        PlaybookTable(
            id=uuid4(),
            title="Archived title",
            slug="archived-title",
            problem="p",
            procedure="pr",
            tags=[],
            status=PlaybookStatus.ARCHIVED.value,
            created_by=AUDITOR_UUID,
        )
    )
    await db_session.flush()

    titles = await LibrarianEngine(db_session).existing_playbook_titles_lower()
    assert "archived title" not in titles


@pytest.mark.asyncio
async def test_existing_playbook_titles_lower_unbounded_beyond_prompt_cap(
    db_session: AsyncSession,
) -> None:
    """The dedup surface must not silently miss a collision past the
    mining-context prompt excerpt's LIMIT 20 — insert 25 and confirm the
    26th (an exact-case-insensitive dup of the 21st) is still caught."""
    await db_session.execute(delete(PlaybookTable))
    await db_session.commit()
    await _seed(db_session)
    for i in range(25):
        db_session.add(
            PlaybookTable(
                id=uuid4(),
                title=f"Bulk playbook {i}",
                slug=f"bulk-playbook-{i}",
                problem="p",
                procedure="pr",
                tags=[],
                status=PlaybookStatus.DRAFT.value,
                created_by=AUDITOR_UUID,
            )
        )
    await db_session.flush()

    titles = await LibrarianEngine(db_session).existing_playbook_titles_lower()
    assert "bulk playbook 21" in titles


@pytest.mark.asyncio
async def test_existing_playbook_titles_lower_returns_a_set(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(PlaybookTable))
    await db_session.commit()
    result = await LibrarianEngine(db_session).existing_playbook_titles_lower()
    assert isinstance(result, set)

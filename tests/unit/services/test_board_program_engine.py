"""BoardProgramEngine: trigger/dedup/originate/LEARN over the registry.

Mirrors test_roadmap_engine.py's seeding + real-Postgres style, but swaps in
fake originators (monkeypatched into board_programs._ORIGINATORS) so this
suite tests the ENGINE's own dedup/cron/LEARN logic in isolation from
RoadmapEngine/XEngine's own internal guards (covered by their own suites).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.config import settings as cfg
from roboco.db.tables import (
    AgentTable,
    AuditLogTable,
    BoardProgramCycleTable,
    ProjectTable,
    SystemSettingTable,
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.board_programs import (
    PROGRAMS,
    WEEK_SECONDS,
    BoardProgram,
    TriggerKind,
)
from roboco.foundation.policy.content import markers
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskType,
    Team,
)
from roboco.models.base import (
    TaskStatus as TS,
)
from roboco.services import board_programs as bp_module
from roboco.services.board_programs import BoardProgramEngine
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from roboco.services.task import (
    BARFLY_SOURCE,
    CORONER_SOURCE,
    DOGFOOD_SOURCE,
    LIBRARIAN_SOURCE,
    MEGAPHONE_SOURCE,
    MIRROR_SOURCE,
    PERISCOPE_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    SCALES_SOURCE,
    SENTINEL_SOURCE,
    SPACKLE_SOURCE,
    WAR_ROOM_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    TaskCreateRequest,
    TaskService,
    get_task_service,
)
from sqlalchemy import delete, select, text, update

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
PO_UUID = _foundation.AGENTS["product-owner"].uuid
SLUG = "roboco"
ONE = 1
TWO = 2


@pytest_asyncio.fixture(autouse=True)
async def _purge_board_program_pollution(db_session: AsyncSession) -> None:
    """Board Program state (settings-store overrides, ledger rows, open
    roadmap/x_feature exploration tasks) is shared, cross-test-persistent DB
    state — this module's own tests write it mid-test, and the write-route
    integration suite (``test_board_programs_api.py``'s run-now, which
    commits) can leave it behind too. Purge before every test in this file
    so a leftover row never reads back as a false "already open"/"already
    armed" state or collides on a settings-store primary key."""
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
                    CORONER_SOURCE,
                    SENTINEL_SOURCE,
                    SCALES_SOURCE,
                    SPACKLE_SOURCE,
                    MIRROR_SOURCE,
                    MEGAPHONE_SOURCE,
                    LIBRARIAN_SOURCE,
                    WAR_ROOM_SOURCE,
                    BARFLY_SOURCE,
                    DOGFOOD_SOURCE,
                ]
            ),
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


async def _seed(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (PO_UUID, "product-owner", AgentRole.PRODUCT_OWNER, Team.BOARD),
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
    project = ProjectTable(
        name="RoboCo",
        slug=SLUG,
        git_url="https://github.com/x/roboco.git",
        default_branch="master",
        protected_branches=["master"],
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        is_active=True,
    )
    session.add(project)
    await session.flush()


async def _make_exploration(
    session: AsyncSession, *, source: str, status: TS = TS.PENDING
) -> TaskTable:
    project = (
        await session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    task = await get_task_service(session).create(
        TaskCreateRequest(
            title="exploration cycle",
            description="x",
            acceptance_criteria=["propose once"],
            team=Team.BOARD,
            assigned_to=PO_UUID,
            created_by=SYSTEM_UUID,
            task_type=TaskType.ADMINISTRATIVE,
            nature=TaskNature.NON_TECHNICAL,
            estimated_complexity=Complexity.LOW,
            project_id=cast("UUID", project.id),
            status=TS.PENDING,
            source=source,
            confirmed_by_human=False,
        )
    )
    if status != TS.PENDING:
        task.status = status
    await session.flush()
    return task


# ---------------------------------------------------------------------------
# ContentActions.nothing_to_propose's task resolution — a real-DB regression
# suite. ``task_id`` is now REQUIRED and resolved by a direct fetch-by-id
# (mirrors ``curate_vault``), replacing the old ``get_open_board_program_
# exploration_task`` oldest-wins-across-programs query, which was unsound:
# one explorer role owns SEVERAL independently-cadenced programs at once
# (e.g. product_owner owns roadmap/pest_control/spackle/scales/dogfood), so
# several of an agent's exploration tasks are open simultaneously by design.
# The mock-based per-check envelope tests (reason validation, role gate,
# LEARN recording, best-effort failure) live in
# tests/unit/gateway/test_content_actions_nothing_to_propose.py; this suite
# proves the real DB resolution instead.
# ---------------------------------------------------------------------------


def _nothing_to_propose_actions(session: AsyncSession) -> ContentActions:
    return ContentActions(
        ContentActionsDeps(
            task=TaskService(session),
            git=None,
            a2a=None,
            journal=None,
            workspace=None,
            notifications=None,
        )
    )


@pytest.mark.asyncio
async def test_nothing_to_propose_resolves_named_task_not_older_sibling(
    db_session: AsyncSession,
) -> None:
    """DEFECT regression: the SAME agent has two open exploration tasks from
    two DIFFERENT programs at once (product_owner owns both roadmap and
    pest_control) — nothing_to_propose(task_id=...) must complete the task
    NAMED, never an older sibling from an unrelated program."""
    await _seed(db_session)
    older = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    older.created_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.flush()
    newer = await _make_exploration(db_session, source=PEST_CONTROL_SOURCE)

    env = await _nothing_to_propose_actions(db_session).nothing_to_propose(
        agent_id=PO_UUID,
        task_id=cast("UUID", newer.id),
        reason="checked recent rework/findings evidence; nothing rose to a bug",
    )

    assert env.error is None, env.message
    assert env.task_id == str(newer.id)
    assert env.context_briefing["program"] == "pest_control"
    await db_session.refresh(newer)
    await db_session.refresh(older)
    assert newer.status == TS.COMPLETED
    assert older.status == TS.PENDING


@pytest.mark.asyncio
async def test_nothing_to_propose_rejects_task_not_assigned_to_caller(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    task = await _make_exploration(db_session, source=PEST_CONTROL_SOURCE)
    other_agent = uuid4()

    env = await _nothing_to_propose_actions(db_session).nothing_to_propose(
        agent_id=other_agent,
        task_id=cast("UUID", task.id),
        reason="reviewed the candidate list; none were worth a reply",
    )

    assert env.error == "not_authorized"
    await db_session.refresh(task)
    assert task.status == TS.PENDING


@pytest.mark.asyncio
async def test_nothing_to_propose_rejects_terminal_task(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    task = await _make_exploration(
        db_session, source=PEST_CONTROL_SOURCE, status=TS.COMPLETED
    )

    env = await _nothing_to_propose_actions(db_session).nothing_to_propose(
        agent_id=PO_UUID,
        task_id=cast("UUID", task.id),
        reason="reviewed the candidate list; none were worth a reply",
    )

    assert env.error == "invalid_state"
    assert "completed" in (env.message or "")


@pytest.mark.asyncio
async def test_nothing_to_propose_missing_task_is_not_found(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)

    env = await _nothing_to_propose_actions(db_session).nothing_to_propose(
        agent_id=PO_UUID,
        task_id=uuid4(),
        reason="reviewed the candidate list; none were worth a reply",
    )

    assert env.error == "not_found"


@pytest.mark.asyncio
async def test_nothing_to_propose_learn_failure_does_not_poison_completion(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFECT 2 regression: a genuine DB-level failure inside the LEARN
    write's own flush() must not poison the outer transaction — the task
    completion flushed just before it must still survive the real commit
    that follows (mirrors DbCommitMiddleware's post-response commit).

    Uses an actual failing SQL statement, not a bare ``raise RuntimeError``
    — a plain Python exception never touches the DBAPI connection, so it
    would pass even without the ``begin_nested()`` savepoint fix and prove
    nothing. Only a real aborted transaction exercises the isolation.

    A real commit is unavoidable here — every other test in this module
    relies on ``db_session``'s teardown rollback for isolation, so this test
    deletes its own committed rows afterward (the ``_seed``-created project
    has no idempotent-reuse guard, so a leaked commit collides with the next
    test's ``_seed`` call on the unique project slug)."""
    await _seed(db_session)
    task = await _make_exploration(db_session, source=PEST_CONTROL_SOURCE)

    class _PoisonedEngine:
        async def record_nothing_to_propose(self, *_a: object, **_kw: object) -> None:
            await db_session.execute(text("SELECT 1/0"))

    monkeypatch.setattr(
        bp_module, "get_board_program_engine", lambda _s: _PoisonedEngine()
    )

    env = await _nothing_to_propose_actions(db_session).nothing_to_propose(
        agent_id=PO_UUID,
        task_id=cast("UUID", task.id),
        reason="reviewed the candidate list; none were worth a reply",
    )
    assert env.error is None, env.message

    try:
        # Without the savepoint, this commit would raise (the connection is
        # still in Postgres's aborted-transaction state) and everything
        # above, including the task completion, would be lost.
        await db_session.commit()

        refetched = await get_task_service(db_session).get(cast("UUID", task.id))
        assert refetched is not None
        assert refetched.status == TS.COMPLETED
    finally:
        await db_session.execute(delete(TaskTable).where(TaskTable.id == task.id))
        await db_session.execute(delete(ProjectTable).where(ProjectTable.slug == SLUG))
        await db_session.commit()


def _fake_originator(
    holder: dict[str, TaskTable | None],
) -> Callable[[AsyncSession], Awaitable[TaskTable | None]]:
    async def _originate(_session: AsyncSession) -> TaskTable | None:
        return holder["task"]

    return _originate


def _patch_roadmap_originator(
    monkeypatch: pytest.MonkeyPatch, holder: dict[str, TaskTable | None]
) -> None:
    monkeypatch.setitem(bp_module._ORIGINATORS, "roadmap", _fake_originator(holder))


@pytest.mark.asyncio
async def test_disabled_program_never_originates(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", False)
    holder: dict[str, TaskTable | None] = {"task": None}
    _patch_roadmap_originator(monkeypatch, holder)
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert "roadmap" not in opened


@pytest.mark.asyncio
async def test_dormant_with_no_settings_store_rows_and_legacy_flags_off(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No settings-store overrides + both legacy boot flags False: a tick
    originates nothing and writes ZERO ledger rows — the guarantee the
    deleted per-engine dormant-loop tests covered, now at the engine layer
    every program's arming decision routes through."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", False)
    monkeypatch.setattr(cfg, "x_engine_enabled", False)
    monkeypatch.setattr(cfg, "x_feature_spotlight_enabled", False)
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == []

    rows = (await db_session.execute(select(BoardProgramCycleTable))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_open_cycle_blocks_reorigination(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", True)
    task = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
        )
    )
    await db_session.flush()
    holder: dict[str, TaskTable | None] = {"task": None}
    _patch_roadmap_originator(monkeypatch, holder)
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert "roadmap" not in opened


@pytest.mark.asyncio
async def test_due_program_originates_and_opens_cycle_row(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", True)
    new_task = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    holder: dict[str, TaskTable | None] = {"task": new_task}
    _patch_roadmap_originator(monkeypatch, holder)
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == ["roadmap"]

    rows = (
        (
            await db_session.execute(
                select(BoardProgramCycleTable).where(
                    BoardProgramCycleTable.program_key == "roadmap"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == ONE
    assert rows[0].exploration_task_id == new_task.id
    assert rows[0].closed_at is None


@pytest.mark.asyncio
async def test_closed_cycle_past_interval_allows_reorigination(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", True)
    monkeypatch.setattr(cfg, "roadmap_interval_seconds", 300)
    old_task = await _make_exploration(
        db_session, source=ROADMAP_SOURCE, status=TS.COMPLETED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=old_task.id,
            opened_at=datetime.now(UTC) - timedelta(seconds=301),
            closed_at=datetime.now(UTC) - timedelta(seconds=200),
        )
    )
    await db_session.flush()
    new_task = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    holder: dict[str, TaskTable | None] = {"task": new_task}
    _patch_roadmap_originator(monkeypatch, holder)
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == ["roadmap"]


@pytest.mark.asyncio
async def test_auto_closes_open_row_once_task_goes_terminal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale open row whose exploration task already went terminal is
    reconciled (auto-closed) rather than permanently blocking dedup."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", True)
    monkeypatch.setattr(cfg, "roadmap_interval_seconds", 1)
    stale_task = await _make_exploration(
        db_session, source=ROADMAP_SOURCE, status=TS.COMPLETED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=stale_task.id,
            opened_at=datetime.now(UTC) - timedelta(seconds=5),
        )
    )
    await db_session.flush()
    new_task = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    holder: dict[str, TaskTable | None] = {"task": new_task}
    _patch_roadmap_originator(monkeypatch, holder)
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == ["roadmap"]

    rows = (
        (
            await db_session.execute(
                select(BoardProgramCycleTable)
                .where(BoardProgramCycleTable.program_key == "roadmap")
                .order_by(BoardProgramCycleTable.opened_at)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == TWO
    assert rows[0].closed_at is not None  # reconciled


@pytest.mark.asyncio
async def test_record_decision_bumps_counters_and_closes_when_done(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    task = await _make_exploration(
        db_session, source=ROADMAP_SOURCE, status=TS.COMPLETED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
        )
    )
    await db_session.flush()
    engine = BoardProgramEngine(db_session)
    await engine.record_decision("roadmap", "item-1", "approved")
    await engine.record_decision("roadmap", "item-2", "rejected", reason="not now")

    row = await engine._latest_cycle("roadmap")
    assert row is not None
    assert row.items_proposed == TWO
    assert row.items_approved == 1
    assert row.items_rejected == 1
    assert row.closed_at is not None  # exploration task was already terminal
    assert {
        "item_ref": "item-1",
        "verdict": "approved",
        "reason": None,
    } in row.decisions


@pytest.mark.asyncio
async def test_record_decision_targets_named_exploration_over_most_recent(
    db_session: AsyncSession,
) -> None:
    """Two cycle rows exist for "roadmap": the FIRST auto-closed via a
    terminal exploration task with items still undecided (the admin-cancel
    edge), the SECOND opened after it and is the most-recent row. A decision
    carrying the FIRST task's id must land on the FIRST row, not silently
    fall through to the most-recent-cycle fallback."""
    await _seed(db_session)
    first_task = await _make_exploration(
        db_session, source=ROADMAP_SOURCE, status=TS.CANCELLED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=first_task.id,
            opened_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )
    second_task = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=second_task.id,
            opened_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    await engine.record_decision(
        "roadmap",
        "item-1",
        "approved",
        exploration_task_id=cast("UUID", first_task.id),
    )

    first_row = await engine._cycle_for_exploration(
        "roadmap", cast("UUID", first_task.id)
    )
    second_row = await engine._cycle_for_exploration(
        "roadmap", cast("UUID", second_task.id)
    )
    assert first_row is not None
    assert second_row is not None
    assert first_row.items_proposed == ONE
    assert first_row.items_approved == ONE
    assert {
        "item_ref": "item-1",
        "verdict": "approved",
        "reason": None,
    } in first_row.decisions
    assert first_row.closed_at is not None  # reconciled: task was terminal
    assert second_row.items_proposed == 0
    assert second_row.decisions == []


@pytest.mark.asyncio
async def test_prior_cycle_context_renders_rejections_with_reasons(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    assert await BoardProgramEngine(db_session).prior_cycle_context("roadmap") == ""

    task = await _make_exploration(
        db_session, source=ROADMAP_SOURCE, status=TS.COMPLETED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
            closed_at=datetime.now(UTC),
            items_proposed=2,
            items_approved=1,
            items_rejected=1,
            decisions=[
                {"item_ref": "item-1", "verdict": "approved", "reason": None},
                {"item_ref": "item-2", "verdict": "rejected", "reason": "too risky"},
            ],
        )
    )
    await db_session.flush()

    context = await BoardProgramEngine(db_session).prior_cycle_context("roadmap")
    assert "proposed 2, approved 1" in context
    assert "item-2 — too risky" in context


# ---------------------------------------------------------------------------
# record_nothing_to_propose — the nothing_to_propose verb's LEARN write.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_nothing_to_propose_sets_reason_without_touching_counters(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    task = await _make_exploration(
        db_session, source=BARFLY_SOURCE, status=TS.COMPLETED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="barfly",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
        )
    )
    await db_session.flush()
    engine = BoardProgramEngine(db_session)
    await engine.record_nothing_to_propose(
        "barfly", cast("UUID", task.id), "no worthwhile conversations this cycle"
    )

    row = await engine._latest_cycle("barfly")
    assert row is not None
    assert row.nothing_to_propose_reason == "no worthwhile conversations this cycle"
    assert row.items_proposed == 0
    assert row.items_approved == 0
    assert row.items_rejected == 0
    assert row.decisions == []
    # Does not close the row itself — that's still _maybe_close's job.
    assert row.closed_at is None


@pytest.mark.asyncio
async def test_record_nothing_to_propose_noop_when_no_cycle_row(
    db_session: AsyncSession,
) -> None:
    """No cycle row exists for this program/task at all — a best-effort
    no-op, mirroring record_decision's own producers' best-effort wrapping."""
    engine = BoardProgramEngine(db_session)
    await engine.record_nothing_to_propose("barfly", uuid4(), "no candidates")


@pytest.mark.asyncio
async def test_prior_cycle_context_renders_nothing_to_propose_reason(
    db_session: AsyncSession,
) -> None:
    """The load-bearing render: a proposed-0 closed cycle with a recorded
    reason shows WHY in the next cycle's LEARN context, not a bare
    "proposed 0, approved 0"."""
    await _seed(db_session)
    task = await _make_exploration(
        db_session, source=BARFLY_SOURCE, status=TS.COMPLETED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="barfly",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
            closed_at=datetime.now(UTC),
            nothing_to_propose_reason="no worthwhile conversations this cycle",
        )
    )
    await db_session.flush()

    context = await BoardProgramEngine(db_session).prior_cycle_context("barfly")
    assert (
        "proposed 0 — nothing to propose: no worthwhile conversations this cycle"
        in context
    )
    assert "proposed 0, approved 0" not in context


def test_learn_ref_names_the_item_not_its_per_cycle_index() -> None:
    """The ref reaches the next cycle's prompt, so it must say WHAT was
    decided — ``item-1`` means something different in every cycle."""
    assert bp_module.learn_ref({"id": "item-1", "title": "Fix the CLA gate"}) == (
        "Fix the CLA gate"
    )
    # Scales items name the live task they mutate instead of a draft title.
    assert bp_module.learn_ref(
        {"id": "item-0", "target_task_title": "Panel UX wave"}
    ) == ("Panel UX wave")
    # A title-less item degrades to the old behaviour rather than an empty ref.
    assert bp_module.learn_ref({"id": "item-2"}) == "item-2"
    assert bp_module.learn_ref({"id": "item-3", "title": "   "}) == "item-3"
    cap = 80
    assert len(bp_module.learn_ref({"id": "item-4", "title": "x" * 200})) == cap


def test_originators_cover_exactly_the_registry() -> None:
    assert set(bp_module._ORIGINATORS) == set(PROGRAMS)


def test_program_sources_match_service_layer_constants() -> None:
    assert PROGRAMS["roadmap"].source == ROADMAP_SOURCE
    assert PROGRAMS["x_feature"].source == X_FEATURE_EXPLORATION_SOURCE
    assert PROGRAMS["pest_control"].source == PEST_CONTROL_SOURCE
    assert PROGRAMS["periscope"].source == PERISCOPE_SOURCE
    assert PROGRAMS["coroner"].source == CORONER_SOURCE
    assert PROGRAMS["sentinel"].source == SENTINEL_SOURCE
    assert PROGRAMS["spackle"].source == SPACKLE_SOURCE
    assert PROGRAMS["scales"].source == SCALES_SOURCE
    assert PROGRAMS["mirror"].source == MIRROR_SOURCE
    assert PROGRAMS["megaphone"].source == MEGAPHONE_SOURCE
    assert PROGRAMS["librarian"].source == LIBRARIAN_SOURCE
    assert PROGRAMS["war_room"].source == WAR_ROOM_SOURCE
    assert PROGRAMS["barfly"].source == BARFLY_SOURCE
    assert PROGRAMS["dogfood"].source == DOGFOOD_SOURCE


@pytest.mark.asyncio
async def test_coroner_originator_is_a_never_originating_stub(
    db_session: AsyncSession,
) -> None:
    """EVENT programs are never cron/metric-originated — the registered
    ``_ORIGINATORS["coroner"]`` callable exists only so the parity test above
    holds; it must always return None (a real cycle opens via
    ``CoronerEngine.open_for_incident``, bypassing this dict — see
    ``_originate_coroner``'s docstring)."""
    assert await bp_module._ORIGINATORS["coroner"](db_session) is None


@pytest.mark.asyncio
async def test_run_due_programs_never_opens_coroner_even_when_armed(
    db_session: AsyncSession,
) -> None:
    """EVENT programs are opened only by their own hooks, never the loop —
    ``run_due_programs`` must skip ``coroner`` entirely regardless of
    arming, mirroring ``test_program_due_event_never_cron_fires`` at the
    foundation layer."""
    db_session.add(
        SystemSettingTable(key="board_program.coroner.enabled", value="true")
    )
    await db_session.flush()
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert "coroner" not in opened


def _patch_war_room_originator(
    monkeypatch: pytest.MonkeyPatch, holder: dict[str, TaskTable | None]
) -> None:
    monkeypatch.setitem(bp_module._ORIGINATORS, "war_room", _fake_originator(holder))


@pytest.mark.asyncio
async def test_war_room_originator_is_real_unarmed_no_op(
    db_session: AsyncSession,
) -> None:
    """Unlike Coroner's always-None ``_originate_coroner`` stub, War Room's
    ``_ORIGINATORS["war_room"]`` entry genuinely calls into
    ``WarRoomEngine.run_cycle`` — proven by NOT patching it here: it returns
    None because the program isn't armed in this bare session, a real
    arming decision (see test_war_room_engine.py for the engine's own full
    arm/creds/dedup coverage), not a hardcoded stub."""
    assert await bp_module._ORIGINATORS["war_room"](db_session) is None


@pytest.mark.asyncio
async def test_run_due_programs_never_opens_war_room_even_when_armed(
    db_session: AsyncSession,
) -> None:
    """EVENT programs are opened only by their own hooks, never the loop —
    ``run_due_programs`` must skip ``war_room`` entirely regardless of
    arming. War Room's originator is REAL (unlike coroner's stub), so this
    specifically proves the trigger-kind guard in ``run_due_programs``
    itself — not an originator that happens to no-op."""
    db_session.add(
        SystemSettingTable(key="board_program.war_room.enabled", value="true")
    )
    await db_session.flush()
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert "war_room" not in opened


@pytest.mark.asyncio
async def test_run_due_programs_never_opens_dogfood_even_when_armed(
    db_session: AsyncSession,
) -> None:
    """EVENT programs are opened only by their own hooks/run-now, never the
    cron loop — ``run_due_programs`` must skip ``dogfood`` entirely
    regardless of arming, mirroring ``test_run_due_programs_never_opens_
    coroner_even_when_armed``. Unlike Coroner, Dogfood DOES have a real
    project-scoped originator (see ``test_open_program_cycle_originates_
    dogfood_for_real`` below) — this proves the cron loop's own trigger-kind
    gate is what blocks it, not a missing opt-in."""
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    project.board_programs = ["dogfood"]
    db_session.add(
        SystemSettingTable(key="board_program.dogfood.enabled", value="true")
    )
    await db_session.flush()
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert "dogfood" not in opened


@pytest.mark.asyncio
async def test_open_program_cycle_drives_war_room_via_real_originator(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE run-now-works-for-EVENT proof: ``open_program_cycle`` never
    checks trigger kind at all, so once war_room is armed + dedup-clear it
    genuinely originates through the REAL ``_ORIGINATORS["war_room"]``
    entry — unlike coroner, whose run-now would 409 forever (its originator
    is a stub that always returns None)."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    db_session.add(
        SystemSettingTable(key="board_program.war_room.enabled", value="true")
    )
    new_task = await _make_exploration(db_session, source=WAR_ROOM_SOURCE)
    holder: dict[str, TaskTable | None] = {"task": new_task}
    _patch_war_room_originator(monkeypatch, holder)
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    task = await engine.open_program_cycle("war_room")
    assert task is not None
    assert task.id == new_task.id

    rows = (
        (
            await db_session.execute(
                select(BoardProgramCycleTable).where(
                    BoardProgramCycleTable.program_key == "war_room"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == ONE


@pytest.mark.asyncio
async def test_open_program_cycle_originates_dogfood_for_real(
    db_session: AsyncSession,
) -> None:
    """Unlike Coroner's never-firing stub, Dogfood registers a REAL
    originator (``roboco.services.board_programs._originate_dogfood``) — a
    CEO "run now" (and the release-publish hook, which calls the exact same
    ``open_program_cycle`` path) must actually open a cycle when armed and
    an opted-in project exists."""
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    project.board_programs = ["dogfood"]
    db_session.add(
        SystemSettingTable(key="board_program.dogfood.enabled", value="true")
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    task = await engine.open_program_cycle("dogfood")
    assert task is not None
    assert task.source == DOGFOOD_SOURCE
    assert task.project_id == project.id


# ---------------------------------------------------------------------------
# Task 6b: per-project program scoping
# ---------------------------------------------------------------------------

_PEST_CONTROL = BoardProgram(
    key="pest_control",
    role="product_owner",
    trigger=TriggerKind.CRON,
    source="board_pest_control",
    default_interval_seconds=1,
    scope="project",
)


def _arm_setting(session: AsyncSession, key: str) -> None:
    """Bypass ``SettingsService.set``'s key allowlist (a project-scoped test
    program is never a real writable key) and write the raw row directly —
    ``get_bool`` only reads it, it never validates."""
    session.add(SystemSettingTable(key=key, value="true"))


@pytest.mark.asyncio
async def test_opted_in_projects_filters_by_project_participates(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    opted_in = ProjectTable(
        name="Opted In",
        slug="opted-in-proj",
        git_url="https://github.com/x/opted-in.git",
        default_branch="master",
        protected_branches=["master"],
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        is_active=True,
        board_programs=["pest_control"],
    )
    db_session.add(opted_in)
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    projects = await engine.opted_in_projects(_PEST_CONTROL)
    # SLUG ("roboco", seeded by _seed) never opted in — only the new project.
    assert {p.slug for p in projects} == {"opted-in-proj"}


@pytest.mark.asyncio
async def test_run_due_programs_skips_project_scoped_program_with_no_opt_in(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _PEST_CONTROL)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    holder: dict[str, TaskTable | None] = {"task": None}
    monkeypatch.setitem(
        bp_module._ORIGINATORS, "pest_control", _fake_originator(holder)
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert "pest_control" not in opened

    rows = (
        (
            await db_session.execute(
                select(BoardProgramCycleTable).where(
                    BoardProgramCycleTable.program_key == "pest_control"
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_open_program_cycle_returns_none_with_no_project_opted_in(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _PEST_CONTROL)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    assert await engine.open_program_cycle("pest_control") is None


@pytest.mark.asyncio
async def test_run_due_programs_originates_project_scoped_program_with_opt_in(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    project.board_programs = ["pest_control"]
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _PEST_CONTROL)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    new_task = await _make_exploration(db_session, source="board_pest_control")
    holder: dict[str, TaskTable | None] = {"task": new_task}
    monkeypatch.setitem(
        bp_module._ORIGINATORS, "pest_control", _fake_originator(holder)
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == ["pest_control"]


# ---------------------------------------------------------------------------
# Pest Control's metric-predicate accelerator (spec §4: "weekly cron OR
# rework-rate spike") — the predicate opens a cycle off-schedule, still gated
# by enabled + scope + dedup exactly like the cron path.
# ---------------------------------------------------------------------------

# A far-future cron cadence so the cron pass never fires within these tests —
# only the metric predicate can open the cycle.
_NEVER_DUE = BoardProgram(
    key="pest_control",
    role="product_owner",
    trigger=TriggerKind.CRON,
    source="board_pest_control",
    default_interval_seconds=WEEK_SECONDS * 100,
    scope="project",
)


async def _seed_recently_closed_cycle(session: AsyncSession) -> None:
    """A CLOSED ledger row opened just now — makes the CRON pass genuinely
    NOT due (recent + a huge interval) so a test can isolate the metric-
    predicate path. Needs no linked task: ``_dedup_state`` never runs the
    auto-close reconciliation on a row whose ``closed_at`` is already set."""
    session.add(
        BoardProgramCycleTable(
            program_key="pest_control",
            exploration_task_id=None,
            opened_at=datetime.now(UTC),
            closed_at=datetime.now(UTC),
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_metric_predicate_opens_cycle_off_schedule_when_it_fires(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    project.board_programs = ["pest_control"]
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _NEVER_DUE)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    await _seed_recently_closed_cycle(db_session)
    new_task = await _make_exploration(db_session, source="board_pest_control")
    holder: dict[str, TaskTable | None] = {"task": new_task}
    monkeypatch.setitem(
        bp_module._ORIGINATORS, "pest_control", _fake_originator(holder)
    )
    monkeypatch.setitem(
        bp_module._METRIC_PREDICATES,
        "pest_control",
        _fake_predicate(True),
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == ["pest_control"]


@pytest.mark.asyncio
async def test_metric_predicate_below_threshold_opens_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    project.board_programs = ["pest_control"]
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _NEVER_DUE)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    await _seed_recently_closed_cycle(db_session)
    monkeypatch.setitem(
        bp_module._METRIC_PREDICATES,
        "pest_control",
        _fake_predicate(False),
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == []


@pytest.mark.asyncio
async def test_metric_predicate_never_consulted_when_disabled(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disabled program's predicate must never even run (cheap, isolated —
    no wasted MetricsService query on a dormant program)."""
    await _seed(db_session)
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _NEVER_DUE)
    called = {"n": 0}

    async def _boom(_session: AsyncSession) -> bool:
        called["n"] += 1
        raise AssertionError("predicate must not run while disabled")

    monkeypatch.setitem(bp_module._METRIC_PREDICATES, "pest_control", _boom)
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == []
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_metric_predicate_never_evaluated_when_dedup_blocked(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An already-open cycle blocks the metric predicate from ever running —
    the cheap dedup gate must run BEFORE the (8-11 query) rework-rate check,
    so an open cycle costs the metrics service nothing on every tick."""
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    project.board_programs = ["pest_control"]
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _NEVER_DUE)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    open_task = await _make_exploration(db_session, source="board_pest_control")
    db_session.add(
        BoardProgramCycleTable(
            program_key="pest_control",
            exploration_task_id=open_task.id,
            opened_at=datetime.now(UTC),
        )
    )
    called = {"n": 0}

    async def _boom(_session: AsyncSession) -> bool:
        called["n"] += 1
        raise AssertionError("predicate must not run while dedup-blocked")

    monkeypatch.setitem(bp_module._METRIC_PREDICATES, "pest_control", _boom)
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == []
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_metric_predicate_never_evaluated_when_scope_empty(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No project opted into pest_control — the cheap scope gate blocks the
    metric predicate from ever running (no project ever opts SLUG in here,
    unlike the sibling tests)."""
    await _seed(db_session)
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _NEVER_DUE)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    called = {"n": 0}

    async def _boom(_session: AsyncSession) -> bool:
        called["n"] += 1
        raise AssertionError("predicate must not run with no project opted in")

    monkeypatch.setitem(bp_module._METRIC_PREDICATES, "pest_control", _boom)
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == []
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_real_rework_predicate_fires_above_threshold(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercises the REAL predicate (not a fake) against MetricsService's
    rework rate — proves the threshold wiring, not just the engine seam."""
    monkeypatch.setattr(cfg, "pest_rework_threshold", 0.3)
    result = await bp_module._pest_control_rework_spike(db_session)
    assert result is False  # no completed/reworked tasks seeded -> rate 0.0


def _fake_predicate(
    verdict: bool,
) -> Callable[[AsyncSession], Awaitable[bool]]:
    async def _predicate(_session: AsyncSession) -> bool:
        return verdict

    return _predicate


# ---------------------------------------------------------------------------
# Item-payload snapshotting (gap 3: the full per-item payload used to live
# ONLY on the exploration task's own orchestration_markers, gone the instant
# TaskService.delete removes it) + the generic audit event (gap 4) +
# list_cycles (gap 5's service-layer read surface).
# ---------------------------------------------------------------------------


async def _seed_mirror_item(
    session: AsyncSession, task: TaskTable, *, materialized_task_id: str
) -> None:
    markers.set_messaging_fixes(
        task,
        {
            "items": [
                {
                    "id": "item-0",
                    "title": "README claims a feature that no longer ships",
                    "evidence": "README section 3 still describes the old flow",
                    "description": "Update the README to match shipped behavior",
                    "acceptance_criteria": ["README section 3 rewritten"],
                    "team": "backend",
                    "project_slug": "roboco",
                    "status": "approved",
                    "materialized_task_id": materialized_task_id,
                }
            ]
        },
    )
    await session.flush()


@pytest.mark.asyncio
async def test_record_decision_stamps_item_snapshot_from_queue_shaped_marker(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    task = await _make_exploration(db_session, source=MIRROR_SOURCE)
    materialized_id = str(uuid4())
    await _seed_mirror_item(db_session, task, materialized_task_id=materialized_id)
    db_session.add(
        BoardProgramCycleTable(
            program_key="mirror",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    await engine.record_decision(
        "mirror",
        "README claims a feature that no longer ships",
        "approved",
        exploration_task_id=cast("UUID", task.id),
    )

    row = await engine._cycle_for_exploration("mirror", cast("UUID", task.id))
    assert row is not None
    snapshot = row.decisions[0]["item_snapshot"]
    assert snapshot["title"] == "README claims a feature that no longer ships"
    assert snapshot["evidence"] == "README section 3 still describes the old flow"
    assert snapshot["description"] == "Update the README to match shipped behavior"
    assert snapshot["acceptance_criteria"] == ["README section 3 rewritten"]
    assert snapshot["status"] == "approved"
    assert snapshot["materialized_task_id"] == materialized_id


@pytest.mark.asyncio
async def test_record_decision_item_snapshot_survives_exploration_task_deletion(
    db_session: AsyncSession,
) -> None:
    """DEFECT regression (gap 3): TaskService.delete hard-deletes the
    exploration task the item payload used to live exclusively on, and the
    cycle row's FK is ondelete=SET NULL, so without the snapshot the
    decision would survive with nothing behind it. Proves the snapshot
    baked in at decision time reads back intact once the task row is gone.

    The FK's ON DELETE SET NULL fires as part of the DELETE statement itself
    (an immediate, non-deferrable constraint), so no real commit is needed
    to observe it, just a DB round-trip. ``refresh()`` re-reads ``cycle`` from
    Postgres inside the SAME still-open, rollback-at-teardown transaction
    every other test in this file relies on, so this needs no leaked-row
    cleanup unlike the real-commit tests elsewhere in this module.
    """
    await _seed(db_session)
    task = await _make_exploration(db_session, source=MIRROR_SOURCE)
    task_id = cast("UUID", task.id)
    await _seed_mirror_item(db_session, task, materialized_task_id=str(uuid4()))
    cycle = BoardProgramCycleTable(
        program_key="mirror", exploration_task_id=task_id, opened_at=datetime.now(UTC)
    )
    db_session.add(cycle)
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    await engine.record_decision(
        "mirror",
        "README claims a feature that no longer ships",
        "approved",
        exploration_task_id=task_id,
    )

    await db_session.execute(delete(TaskTable).where(TaskTable.id == task_id))
    await db_session.flush()
    await db_session.refresh(cycle)

    assert cycle.exploration_task_id is None  # nulled by the FK
    snapshot = cycle.decisions[0]["item_snapshot"]
    assert snapshot["title"] == "README claims a feature that no longer ships"
    assert snapshot["materialized_task_id"]


@pytest.mark.asyncio
async def test_record_decision_caps_an_explicit_item_payload(
    db_session: AsyncSession,
) -> None:
    """An explicit ``item_payload`` (the x_post_service/playbook.py path) is
    bounded the same way as an auto-resolved one: unknown keys are dropped
    and long text is truncated, this is a snapshot, not an archive."""
    await _seed(db_session)
    task = await _make_exploration(
        db_session, source=ROADMAP_SOURCE, status=TS.COMPLETED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    await engine.record_decision(
        "roadmap",
        "item-1",
        "approved",
        item_payload={
            "title": "x" * 500,
            "unexpected_field": "must be dropped",
            "acceptance_criteria": [f"ac-{i}" for i in range(20)],
        },
    )

    row = await engine._latest_cycle("roadmap")
    assert row is not None
    snapshot = row.decisions[0]["item_snapshot"]
    assert len(snapshot["title"]) == bp_module._SNAPSHOT_TEXT_CAP
    assert "unexpected_field" not in snapshot
    assert len(snapshot["acceptance_criteria"]) == bp_module._SNAPSHOT_AC_CAP


@pytest.mark.asyncio
async def test_record_decision_emits_a_generic_audit_event(
    db_session: AsyncSession,
) -> None:
    """Gap 4: every program gets one audit row per decision at the single
    ``record_decision`` chokepoint. Previously only Scales' own
    ``task.scales_rebalance`` execution audit existed, and no program
    (Scales included) emitted anything from LEARN itself."""
    await _seed(db_session)
    task = await _make_exploration(
        db_session, source=ROADMAP_SOURCE, status=TS.COMPLETED
    )
    cycle = BoardProgramCycleTable(
        program_key="roadmap", exploration_task_id=task.id, opened_at=datetime.now(UTC)
    )
    db_session.add(cycle)
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    await engine.record_decision(
        "roadmap", "item-1", "rejected", reason="not aligned with the charter"
    )

    rows = (
        (
            await db_session.execute(
                select(AuditLogTable).where(
                    AuditLogTable.event_type == "board_program.decision",
                    AuditLogTable.target_id == cycle.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].target_type == "board_program_cycle"
    assert rows[0].details["program_key"] == "roadmap"
    assert rows[0].details["verdict"] == "rejected"
    assert rows[0].details["reason"] == "not aligned with the charter"


@pytest.mark.asyncio
async def test_list_cycles_returns_newest_first_and_capped(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    for offset_hours in (3, 1, 2):
        db_session.add(
            BoardProgramCycleTable(
                program_key="mirror",
                exploration_task_id=None,
                opened_at=datetime.now(UTC) - timedelta(hours=offset_hours),
            )
        )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    cycles = await engine.list_cycles("mirror", limit=2)
    assert len(cycles) == TWO
    # Newest (smallest offset) first.
    assert cycles[0].opened_at > cycles[1].opened_at

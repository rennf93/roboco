"""RoadmapService coverage: per-item approve materializes a BACKLOG task
(idempotent), reject records a reason (idempotent), and the exploration task
completes once every item is terminal.

Mirrors the X-post-service / release-proposal-service tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentTable,
    AuditLogTable,
    BoardProgramCycleTable,
    ProjectTable,
    SystemSettingTable,
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.foundation.policy.lifecycle import _next_hint_pr_fail
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    Team,
)
from roboco.models.base import TaskNature as TN
from roboco.models.base import TaskStatus as TS
from roboco.models.base import TaskType as TT
from roboco.services import board_programs as bp_module
from roboco.services.roadmap_service import RoadmapService, get_roadmap_service
from roboco.services.task import (
    ROADMAP_ITEM_SOURCE,
    ROADMAP_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
)
from sqlalchemy import delete, select, update

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
PO_UUID = _foundation.AGENTS["product-owner"].uuid
CEO_UUID = _foundation.AGENTS["ceo"].uuid
MAIN_PM_UUID = _foundation.AGENTS["main-pm"].uuid
ONE = 1
TWO = 2


@pytest_asyncio.fixture(autouse=True)
async def _purge_board_program_pollution(db_session: AsyncSession) -> None:
    """See ``test_board_program_engine.py``'s identical fixture: Board
    Program settings-store rows / ledger rows / open exploration tasks are
    shared, cross-test-persistent DB state that a sibling suite (this
    module's own ``_seed_cycle_ledger_row`` rows, or the write-route
    ``test_board_programs_api.py`` run-now test) can leave behind — this
    module's ``scalar_one()`` ledger lookups need exactly one row. Purge
    before every test in this file."""
    await db_session.execute(
        delete(SystemSettingTable).where(SystemSettingTable.key.like("board_program.%"))
    )
    await db_session.execute(delete(BoardProgramCycleTable))
    await db_session.execute(
        update(TaskTable)
        .where(
            TaskTable.source.in_([ROADMAP_SOURCE, X_FEATURE_EXPLORATION_SOURCE]),
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


def _item(idx: int, *, status: str = "proposed", project_slug: str) -> dict:
    return {
        "id": f"item-{idx}",
        "title": f"Item {idx}",
        "description": f"Description for item {idx} that is long enough",
        "acceptance_criteria": [f"criterion {idx}a", f"criterion {idx}b"],
        "project_slug": project_slug,
        "team": "backend",
        "priority": 2,
        "rationale": f"Rationale for item {idx}",
        "status": status,
        "reject_reason": None,
        "materialized_task_id": None,
    }


async def _seed_agents(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (PO_UUID, "product-owner", AgentRole.PRODUCT_OWNER, Team.BOARD),
        (CEO_UUID, "ceo", AgentRole.CEO, None),
        (MAIN_PM_UUID, "main-pm", AgentRole.MAIN_PM, Team.MAIN_PM),
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


async def _seed_project(session: AsyncSession, slug: str) -> ProjectTable:
    await _seed_agents(session)
    project = ProjectTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        git_url=f"https://example.com/{slug}.git",
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
    )
    session.add(project)
    await session.flush()
    return project


async def _seed_cycle(
    session: AsyncSession, *, items: list[dict] | None = None, project_slug: str
) -> TaskTable:
    await _seed_agents(session)
    task = TaskTable(
        id=uuid4(),
        title="Roadmap exploration cycle",
        description="Explore and propose a themed cycle of roadmap items.",
        acceptance_criteria=["propose_roadmap() called once"],
        status=TS.PENDING,
        priority=2,
        task_type=TT.ADMINISTRATIVE,
        nature=TN.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=SYSTEM_UUID,
        assigned_to=PO_UUID,
        team=Team.BOARD,
        source=ROADMAP_SOURCE,
        confirmed_by_human=False,
    )
    session.add(task)
    await session.flush()
    items = items or [
        _item(0, project_slug=project_slug),
        _item(1, project_slug=project_slug),
    ]
    markers.set_roadmap_cycle(
        task, {"goal": "Close onboarding friction", "items": items}
    )
    await session.flush()
    return task


def _svc(session: AsyncSession) -> RoadmapService:
    return get_roadmap_service(session)


def _id(task: TaskTable) -> UUID:
    return cast("UUID", task.id)


@pytest.mark.asyncio
async def test_approve_materializes_main_pm_owned_task(
    db_session: AsyncSession,
) -> None:
    """Defect fix (#703/#704): approval used to materialize an unowned
    BACKLOG task — nothing dispatches BACKLOG, and once nudged to PENDING a
    cell PM could claim + complete it as a bare root, merging straight to
    the project's head rung and bypassing the Main-PM root / root->master PR
    / CEO approval gate. It now materializes PENDING + assigned_to=main-pm
    instead — team is forced to Team.MAIN_PM too (matching
    TaskService.approve_and_start), since every "is this a coordination
    root" consumer (pr_fail's next-hint, the PR-gate re-delegate steer,
    delegate's wave-chain wiring, the PR labeler) keys on team, not
    assigned_to. Leaving team on the item's own cell was itself the defect:
    the root looked like a bare cell/dev task to all four."""
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(db_session, project_slug="backend-svc")
    result = await _svc(db_session).approve_item(
        _id(task), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"
    assert result.materialized_task_id is not None

    materialized = await db_session.get(TaskTable, result.materialized_task_id)
    assert materialized is not None
    assert materialized.status == TS.PENDING
    assert materialized.assigned_to == MAIN_PM_UUID
    assert materialized.parent_task_id is None
    assert materialized.source == ROADMAP_ITEM_SOURCE
    assert materialized.team == Team.MAIN_PM
    # main_pm can never own a code task (pm_cannot_own_code) — the intake
    # coercion in create_task_from_draft retypes it to planning, the same
    # shape a Main-PM coordination root always carries.
    assert materialized.task_type == TT.PLANNING
    # The item's own cell ("backend") didn't just vanish — it survives as a
    # Notes delegation hint in the composed description, which the Main PM's
    # spawn briefing (_format_task_briefing_block) renders verbatim.
    assert "backend cell" in (materialized.description or "")

    # The predicate the four consumers key on: with team=Team.MAIN_PM and a
    # branch (simulating a claimed root with its assembled PR), pr_fail's
    # next-hint steers the Main PM to re-delegate rather than the nonsensical
    # "dev will revise" hint a Main PM (no code-revise verb) can't act on.

    materialized.branch_name = "feature/main_pm/deadbeef"
    hint = _next_hint_pr_fail(materialized)
    assert "re-delegate" in hint
    assert "do NOT re-submit" in hint

    await db_session.refresh(task)
    payload = markers.get_roadmap_cycle(task)
    assert payload is not None
    item0 = next(i for i in payload["items"] if i["id"] == "item-0")
    assert item0["status"] == "approved"
    assert item0["materialized_task_id"] == result.materialized_task_id


@pytest.mark.asyncio
async def test_approve_is_idempotent(db_session: AsyncSession) -> None:
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(db_session, project_slug="backend-svc")
    svc = _svc(db_session)
    first = await svc.approve_item(_id(task), "item-0", created_by=CEO_UUID)
    second = await svc.approve_item(_id(task), "item-0", created_by=CEO_UUID)
    assert first is not None
    assert second is not None
    assert second.status == "already_approved"
    assert second.materialized_task_id == first.materialized_task_id

    # No duplicate BACKLOG task was created for the second approve — scoped to
    # this item's own (test-unique) title since the source column is shared
    # across the whole (persistent, cross-test) database.
    result = await db_session.execute(
        select(TaskTable).where(
            TaskTable.source == ROADMAP_ITEM_SOURCE, TaskTable.title == "Item 0"
        )
    )
    assert len(result.scalars().all()) == ONE


@pytest.mark.asyncio
async def test_reject_records_reason(db_session: AsyncSession) -> None:
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(db_session, project_slug="backend-svc")
    result = await _svc(db_session).reject_item(
        _id(task), "item-0", "not a priority this quarter"
    )
    assert result is not None
    assert result.status == "rejected"

    await db_session.refresh(task)
    payload = markers.get_roadmap_cycle(task)
    assert payload is not None
    item0 = next(i for i in payload["items"] if i["id"] == "item-0")
    assert item0["status"] == "rejected"
    assert item0["reject_reason"] == "not a priority this quarter"


@pytest.mark.asyncio
async def test_reject_is_idempotent(db_session: AsyncSession) -> None:
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(db_session, project_slug="backend-svc")
    svc = _svc(db_session)
    await svc.reject_item(_id(task), "item-0", "reason one")
    second = await svc.reject_item(_id(task), "item-0", "reason two")
    assert second is not None
    assert second.status == "already_rejected"


@pytest.mark.asyncio
async def test_cannot_reject_an_approved_item(db_session: AsyncSession) -> None:
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(db_session, project_slug="backend-svc")
    svc = _svc(db_session)
    await svc.approve_item(_id(task), "item-0", created_by=CEO_UUID)
    result = await svc.reject_item(_id(task), "item-0", "changed my mind")
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_cannot_approve_a_rejected_item(db_session: AsyncSession) -> None:
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(db_session, project_slug="backend-svc")
    svc = _svc(db_session)
    await svc.reject_item(_id(task), "item-0", "not now")
    result = await svc.approve_item(_id(task), "item-0", created_by=CEO_UUID)
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_all_items_terminal_completes_exploration_task(
    db_session: AsyncSession,
) -> None:
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(db_session, project_slug="backend-svc")
    svc = _svc(db_session)
    await svc.approve_item(_id(task), "item-0", created_by=CEO_UUID)
    assert task.status == TS.PENDING  # one item still proposed
    await svc.reject_item(_id(task), "item-1", "not now")
    assert task.status == TS.COMPLETED  # both items terminal


@pytest.mark.asyncio
async def test_approve_unknown_project_slug_is_invalid_state(
    db_session: AsyncSession,
) -> None:
    task = await _seed_cycle(db_session, project_slug="no-such-project")
    result = await _svc(db_session).approve_item(
        _id(task), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_approve_excluded_project_is_invalid_state(
    db_session: AsyncSession,
) -> None:
    """Task 6b: a project carrying '!roadmap' refuses materialize-side, even
    if propose_roadmap's own point-in-time check somehow let the item
    through (e.g. the project was excluded AFTER the PO proposed it)."""
    project = await _seed_project(db_session, "excluded-svc")
    project.board_programs = ["!roadmap"]
    await db_session.flush()
    task = await _seed_cycle(db_session, project_slug="excluded-svc")
    result = await _svc(db_session).approve_item(
        _id(task), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "invalid_state"
    assert "excluded" in result.detail


@pytest.mark.asyncio
async def test_unknown_task_returns_none(db_session: AsyncSession) -> None:
    result = await _svc(db_session).approve_item(uuid4(), "item-0", created_by=CEO_UUID)
    assert result is None


@pytest.mark.asyncio
async def test_unknown_item_id_returns_none(db_session: AsyncSession) -> None:
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(db_session, project_slug="backend-svc")
    result = await _svc(db_session).approve_item(
        _id(task), "item-999", created_by=CEO_UUID
    )
    assert result is None


@pytest.mark.asyncio
async def test_list_open_cycles_excludes_completed(db_session: AsyncSession) -> None:
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(
        db_session,
        items=[_item(0, project_slug="backend-svc")],
        project_slug="backend-svc",
    )
    svc = _svc(db_session)
    open_before = await svc.list_open_cycles()
    assert task.id in {t.id for t in open_before}
    await svc.approve_item(_id(task), "item-0", created_by=CEO_UUID)
    open_after = await svc.list_open_cycles()
    assert task.id not in {t.id for t in open_after}


@pytest.mark.asyncio
async def test_maybe_complete_cycle_emits_audit(db_session: AsyncSession) -> None:
    # All items terminal -> task completed + a task.completed audit row.
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(db_session, project_slug="backend-svc")
    svc = _svc(db_session)
    await svc.approve_item(_id(task), "item-0", created_by=CEO_UUID)
    assert task.status == TS.PENDING  # one item still proposed
    await svc.reject_item(_id(task), "item-1", "not now")
    assert cast("TS", task.status) == TS.COMPLETED  # both items terminal

    rows = (
        (
            await db_session.execute(
                select(AuditLogTable).where(AuditLogTable.target_id == task.id)
            )
        )
        .scalars()
        .all()
    )
    audit = [
        r
        for r in rows
        if r.event_type == "task.completed"
        or str(r.details.get("to_status", "")).lower() == "completed"
    ]
    assert audit, (
        "expected a task.completed audit row for the PENDING -> COMPLETED transition"
    )


# --------------------------------------------------------------------------- #
# LEARN wiring (Task 5): approve/reject best-effort record onto the open
# board_program_cycles row for "roadmap" — see test_board_program_engine.py
# for record_decision's own counter/close-on-terminal coverage.
# --------------------------------------------------------------------------- #


async def _seed_cycle_ledger_row(session: AsyncSession, task: TaskTable) -> None:
    session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_approve_records_learn_decision(db_session: AsyncSession) -> None:
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(db_session, project_slug="backend-svc")
    await _seed_cycle_ledger_row(db_session, task)
    await _svc(db_session).approve_item(_id(task), "item-0", created_by=CEO_UUID)

    row = (
        await db_session.execute(
            select(BoardProgramCycleTable).where(
                BoardProgramCycleTable.program_key == "roadmap"
            )
        )
    ).scalar_one()
    assert row.items_approved == ONE
    # The ref is the item's TITLE, not its per-cycle index: this row is
    # rendered into the next cycle's exploration prompt, where "item-0"
    # names nothing (see BoardProgramEngine.learn_ref).
    decision = next(d for d in row.decisions if d["item_ref"] == "Item 0")
    assert decision["verdict"] == "approved"
    assert decision["reason"] is None
    # item_snapshot is auto-resolved from the exploration task's own marker
    # payload (BoardProgramEngine._resolve_queue_item_snapshot), proving the
    # item payload now survives the exploration task's eventual deletion.
    assert decision["item_snapshot"]["title"] == "Item 0"


@pytest.mark.asyncio
async def test_reject_records_learn_decision_with_reason(
    db_session: AsyncSession,
) -> None:
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(db_session, project_slug="backend-svc")
    await _seed_cycle_ledger_row(db_session, task)
    await _svc(db_session).reject_item(_id(task), "item-0", "not a priority")

    row = (
        await db_session.execute(
            select(BoardProgramCycleTable).where(
                BoardProgramCycleTable.program_key == "roadmap"
            )
        )
    ).scalar_one()
    assert row.items_rejected == ONE
    # Title, not index — the reject reason is only useful to the next cycle
    # if it says which proposal it was about.
    decision = next(d for d in row.decisions if d["item_ref"] == "Item 0")
    assert decision["verdict"] == "rejected"
    assert decision["reason"] == "not a priority"
    assert decision["item_snapshot"]["reject_reason"] == "not a priority"


@pytest.mark.asyncio
async def test_approve_survives_learn_recording_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record_decision blow-up must never break the CEO's approve."""
    await _seed_project(db_session, "backend-svc")
    task = await _seed_cycle(db_session, project_slug="backend-svc")
    await _seed_cycle_ledger_row(db_session, task)

    async def _boom(_self: object, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("learn boom")

    monkeypatch.setattr(bp_module.BoardProgramEngine, "record_decision", _boom)
    result = await _svc(db_session).approve_item(
        _id(task), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"

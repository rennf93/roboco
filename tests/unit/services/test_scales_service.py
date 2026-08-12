"""ScalesService coverage: per-item approve EXECUTES the item's action
against a live target task (reprioritize its priority, or cancel it) —
idempotent; reject records a reason (idempotent, never touches the target);
the exploration task completes once every item is terminal.

Mirrors test_pest_control_service.py — the structural difference is that
approval mutates an existing task in place instead of materializing a new
one, so there is no ``materialized_task_id`` and no project-participation
gate (a rebalance item targets any live task, not a new draft against an
opted-in project).
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
from roboco.services.scales_service import ScalesService, get_scales_service
from roboco.services.task import (
    CORONER_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    SCALES_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
)
from sqlalchemy import delete, select, update

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
PO_UUID = _foundation.AGENTS["product-owner"].uuid
CEO_UUID = _foundation.AGENTS["ceo"].uuid
ONE = 1
TWO = 2


@pytest_asyncio.fixture(autouse=True)
async def _purge_board_program_pollution(db_session: AsyncSession) -> None:
    """See test_board_program_engine.py's identical fixture."""
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
                    CORONER_SOURCE,
                    SCALES_SOURCE,
                ]
            ),
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


async def _seed_agents(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (PO_UUID, "product-owner", AgentRole.PRODUCT_OWNER, Team.BOARD),
        (CEO_UUID, "ceo", AgentRole.CEO, None),
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


async def _seed_target_task(
    session: AsyncSession,
    project: ProjectTable,
    *,
    title: str,
    status: TS = TS.BACKLOG,
    priority: int = 2,
) -> TaskTable:
    task = TaskTable(
        id=uuid4(),
        title=title,
        description="A live backlog task the rebalance plan targets",
        acceptance_criteria=["done"],
        status=status,
        priority=priority,
        task_type=TT.CODE,
        nature=TN.TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=SYSTEM_UUID,
        project_id=project.id,
        team=Team.BACKEND,
    )
    session.add(task)
    await session.flush()
    return task


def _item(
    idx: int,
    target: TaskTable,
    *,
    action: str = "reprioritize",
    new_priority: int | None = 0,
    status: str = "proposed",
) -> dict:
    return {
        "id": f"item-{idx}",
        "task_ref": str(target.id)[:8],
        "target_task_id": str(target.id),
        "target_task_title": target.title,
        "action": action,
        "new_priority": new_priority if action == "reprioritize" else None,
        "rationale": f"Rationale for item {idx} — a substantive reason",
        "status": status,
        "reject_reason": None,
        "executed_detail": None,
    }


async def _seed_cycle(session: AsyncSession, *, items: list[dict]) -> TaskTable:
    await _seed_agents(session)
    task = TaskTable(
        id=uuid4(),
        title="Scales portfolio-rebalance cycle",
        description="Review the live backlog and propose a rebalance.",
        acceptance_criteria=["propose_rebalance() called once"],
        status=TS.PENDING,
        priority=2,
        task_type=TT.ADMINISTRATIVE,
        nature=TN.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=SYSTEM_UUID,
        assigned_to=PO_UUID,
        team=Team.BOARD,
        source=SCALES_SOURCE,
        confirmed_by_human=False,
    )
    session.add(task)
    await session.flush()
    markers.set_rebalance_plan(task, {"items": items})
    await session.flush()
    return task


def _svc(session: AsyncSession) -> ScalesService:
    return get_scales_service(session)


def _id(task: TaskTable) -> UUID:
    return cast("UUID", task.id)


@pytest.mark.asyncio
async def test_approve_reprioritize_changes_target_priority(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(
        db_session, project, title="Stale task", priority=2
    )
    cycle = await _seed_cycle(
        db_session, items=[_item(0, target, action="reprioritize", new_priority=0)]
    )
    result = await _svc(db_session).approve_item(
        _id(cycle), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"
    assert result.executed_detail == "priority changed to P0"

    await db_session.refresh(target)
    assert target.priority == 0
    assert target.status == TS.BACKLOG  # untouched otherwise

    await db_session.refresh(cycle)
    payload = markers.get_rebalance_plan(cycle)
    assert payload is not None
    item0 = next(i for i in payload["items"] if i["id"] == "item-0")
    assert item0["status"] == "approved"
    assert item0["executed_detail"] == "priority changed to P0"


@pytest.mark.asyncio
async def test_approve_cancel_cancels_target_task(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(db_session, project, title="Dead weight task")
    cycle = await _seed_cycle(db_session, items=[_item(0, target, action="cancel")])
    result = await _svc(db_session).approve_item(
        _id(cycle), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"
    assert result.executed_detail == "cancelled"

    await db_session.refresh(target)
    assert target.status == TS.CANCELLED


@pytest.mark.asyncio
async def test_approve_is_idempotent(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(db_session, project, title="Stale task")
    cycle = await _seed_cycle(
        db_session, items=[_item(0, target, action="reprioritize", new_priority=0)]
    )
    svc = _svc(db_session)
    first = await svc.approve_item(_id(cycle), "item-0", created_by=CEO_UUID)
    second = await svc.approve_item(_id(cycle), "item-0", created_by=CEO_UUID)
    assert first is not None
    assert second is not None
    assert second.status == "already_approved"
    assert second.executed_detail == first.executed_detail

    await db_session.refresh(target)
    assert target.priority == 0  # only ever applied once


@pytest.mark.asyncio
async def test_reject_records_reason_and_leaves_target_untouched(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(
        db_session, project, title="Stale task", priority=2
    )
    cycle = await _seed_cycle(
        db_session, items=[_item(0, target, action="reprioritize", new_priority=0)]
    )
    result = await _svc(db_session).reject_item(
        _id(cycle), "item-0", "still worth doing at current priority"
    )
    assert result is not None
    assert result.status == "rejected"

    await db_session.refresh(target)
    assert target.priority == TWO  # untouched

    await db_session.refresh(cycle)
    payload = markers.get_rebalance_plan(cycle)
    assert payload is not None
    item0 = next(i for i in payload["items"] if i["id"] == "item-0")
    assert item0["status"] == "rejected"
    assert item0["reject_reason"] == "still worth doing at current priority"


@pytest.mark.asyncio
async def test_reject_is_idempotent(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(db_session, project, title="Stale task")
    cycle = await _seed_cycle(db_session, items=[_item(0, target, action="cancel")])
    svc = _svc(db_session)
    await svc.reject_item(_id(cycle), "item-0", "reason one")
    second = await svc.reject_item(_id(cycle), "item-0", "reason two")
    assert second is not None
    assert second.status == "already_rejected"


@pytest.mark.asyncio
async def test_cannot_reject_an_approved_item(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(db_session, project, title="Stale task")
    cycle = await _seed_cycle(db_session, items=[_item(0, target, action="cancel")])
    svc = _svc(db_session)
    await svc.approve_item(_id(cycle), "item-0", created_by=CEO_UUID)
    result = await svc.reject_item(_id(cycle), "item-0", "changed my mind")
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_cannot_approve_a_rejected_item(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(db_session, project, title="Stale task")
    cycle = await _seed_cycle(db_session, items=[_item(0, target, action="cancel")])
    svc = _svc(db_session)
    await svc.reject_item(_id(cycle), "item-0", "not now")
    result = await svc.approve_item(_id(cycle), "item-0", created_by=CEO_UUID)
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_all_items_terminal_completes_exploration_task(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target0 = await _seed_target_task(db_session, project, title="Task A")
    target1 = await _seed_target_task(db_session, project, title="Task B")
    cycle = await _seed_cycle(
        db_session,
        items=[
            _item(0, target0, action="cancel"),
            _item(1, target1, action="reprioritize", new_priority=1),
        ],
    )
    svc = _svc(db_session)
    await svc.approve_item(_id(cycle), "item-0", created_by=CEO_UUID)
    assert cycle.status == TS.PENDING  # one item still proposed
    await svc.reject_item(_id(cycle), "item-1", "not now")
    assert cycle.status == TS.COMPLETED  # both items terminal


@pytest.mark.asyncio
async def test_approve_target_already_claimed_is_invalid_state(
    db_session: AsyncSession,
) -> None:
    """The target left BACKLOG/PENDING since propose time (e.g. a PM claimed
    it) — approve refuses instead of silently mutating in-flight work."""
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(
        db_session, project, title="Now claimed", status=TS.IN_PROGRESS
    )
    cycle = await _seed_cycle(
        db_session, items=[_item(0, target, action="reprioritize", new_priority=0)]
    )
    result = await _svc(db_session).approve_item(
        _id(cycle), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_approve_missing_target_is_invalid_state(
    db_session: AsyncSession,
) -> None:
    await _seed_agents(db_session)
    ghost = {
        "id": "item-0",
        "task_ref": "deadbeef",
        "target_task_id": str(uuid4()),
        "target_task_title": "Gone",
        "action": "cancel",
        "new_priority": None,
        "rationale": "Doesn't matter, target is gone",
        "status": "proposed",
        "reject_reason": None,
        "executed_detail": None,
    }
    cycle = await _seed_cycle(db_session, items=[ghost])
    result = await _svc(db_session).approve_item(
        _id(cycle), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_unknown_task_returns_none(db_session: AsyncSession) -> None:
    result = await _svc(db_session).approve_item(uuid4(), "item-0", created_by=CEO_UUID)
    assert result is None


@pytest.mark.asyncio
async def test_unknown_item_id_returns_none(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(db_session, project, title="Stale task")
    cycle = await _seed_cycle(db_session, items=[_item(0, target, action="cancel")])
    result = await _svc(db_session).approve_item(
        _id(cycle), "item-999", created_by=CEO_UUID
    )
    assert result is None


@pytest.mark.asyncio
async def test_list_open_cycles_excludes_completed(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(db_session, project, title="Stale task")
    cycle = await _seed_cycle(db_session, items=[_item(0, target, action="cancel")])
    svc = _svc(db_session)
    open_before = await svc.list_open_cycles()
    assert cycle.id in {t.id for t in open_before}
    await svc.approve_item(_id(cycle), "item-0", created_by=CEO_UUID)
    open_after = await svc.list_open_cycles()
    assert cycle.id not in {t.id for t in open_after}


@pytest.mark.asyncio
async def test_maybe_complete_cycle_emits_audit(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target0 = await _seed_target_task(db_session, project, title="Task A")
    target1 = await _seed_target_task(db_session, project, title="Task B")
    cycle = await _seed_cycle(
        db_session,
        items=[
            _item(0, target0, action="cancel"),
            _item(1, target1, action="reprioritize", new_priority=1),
        ],
    )
    svc = _svc(db_session)
    await svc.approve_item(_id(cycle), "item-0", created_by=CEO_UUID)
    await svc.reject_item(_id(cycle), "item-1", "not now")
    assert cycle.status == TS.COMPLETED

    rows = (
        (
            await db_session.execute(
                select(AuditLogTable).where(AuditLogTable.target_id == cycle.id)
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


@pytest.mark.asyncio
async def test_approve_emits_rebalance_execution_audit(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(db_session, project, title="Stale task")
    cycle = await _seed_cycle(
        db_session, items=[_item(0, target, action="reprioritize", new_priority=0)]
    )
    await _svc(db_session).approve_item(_id(cycle), "item-0", created_by=CEO_UUID)

    rows = (
        (
            await db_session.execute(
                select(AuditLogTable).where(AuditLogTable.target_id == target.id)
            )
        )
        .scalars()
        .all()
    )
    assert any(r.event_type == "task.scales_rebalance" for r in rows)


# --------------------------------------------------------------------------- #
# LEARN wiring: approve/reject best-effort record onto the open
# board_program_cycles row for "scales".
# --------------------------------------------------------------------------- #


async def _seed_cycle_ledger_row(session: AsyncSession, task: TaskTable) -> None:
    session.add(
        BoardProgramCycleTable(
            program_key="scales",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_approve_records_learn_decision(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(db_session, project, title="Stale task")
    cycle = await _seed_cycle(
        db_session, items=[_item(0, target, action="reprioritize", new_priority=0)]
    )
    await _seed_cycle_ledger_row(db_session, cycle)
    await _svc(db_session).approve_item(_id(cycle), "item-0", created_by=CEO_UUID)

    row = (
        await db_session.execute(
            select(BoardProgramCycleTable).where(
                BoardProgramCycleTable.program_key == "scales"
            )
        )
    ).scalar_one()
    assert row.items_approved == ONE
    # The ref is the item's TITLE, not its per-cycle index: this row is
    # rendered into the next cycle's exploration prompt, where "item-0"
    # names nothing (see BoardProgramEngine.learn_ref).
    decision = next(d for d in row.decisions if d["item_ref"] == "Stale task")
    assert decision["verdict"] == "approved"
    assert decision["reason"] is None
    # Scales items carry no direct materialized_task_id, so the snapshot
    # falls back to target_task_id (the live task the decision acted on).
    assert decision["item_snapshot"]["materialized_task_id"] == str(target.id)


@pytest.mark.asyncio
async def test_reject_records_learn_decision_with_reason(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(db_session, project, title="Stale task")
    cycle = await _seed_cycle(db_session, items=[_item(0, target, action="cancel")])
    await _seed_cycle_ledger_row(db_session, cycle)
    await _svc(db_session).reject_item(_id(cycle), "item-0", "not a priority")

    row = (
        await db_session.execute(
            select(BoardProgramCycleTable).where(
                BoardProgramCycleTable.program_key == "scales"
            )
        )
    ).scalar_one()
    assert row.items_rejected == ONE
    # The ref is the item's TITLE, not its per-cycle index: this row is
    # rendered into the next cycle's exploration prompt, where "item-0"
    # names nothing (see BoardProgramEngine.learn_ref).
    decision = next(d for d in row.decisions if d["item_ref"] == "Stale task")
    assert decision["verdict"] == "rejected"
    assert decision["reason"] == "not a priority"
    assert decision["item_snapshot"]["reject_reason"] == "not a priority"


@pytest.mark.asyncio
async def test_approve_survives_learn_recording_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record_decision blow-up must never break the CEO's approve."""
    project = await _seed_project(db_session, "backend-svc")
    target = await _seed_target_task(db_session, project, title="Stale task")
    cycle = await _seed_cycle(db_session, items=[_item(0, target, action="cancel")])
    await _seed_cycle_ledger_row(db_session, cycle)

    async def _boom(_self: object, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("learn boom")

    monkeypatch.setattr(bp_module.BoardProgramEngine, "record_decision", _boom)
    result = await _svc(db_session).approve_item(
        _id(cycle), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"

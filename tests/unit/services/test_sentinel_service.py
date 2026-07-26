"""SentinelService coverage: per-item approve materializes a Main-PM-owned
root task (idempotent), reject records a reason (idempotent). Mirrors
test_periscope_service.py exactly — the exploration task is ALREADY
COMPLETED (complete-at-propose), so only the item's own status is under
test, plus the docs-area task_type override (mirrors MirrorService).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.config import settings as cfg
from roboco.db.tables import (
    AgentTable,
    BoardProgramCycleTable,
    ProjectTable,
    SystemSettingTable,
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.foundation.policy.lifecycle import _next_hint_pr_fail
from roboco.models.base import AgentRole, AgentStatus, Complexity, Team
from roboco.models.base import TaskNature as TN
from roboco.models.base import TaskStatus as TS
from roboco.models.base import TaskType as TT
from roboco.services import board_programs as bp_module
from roboco.services.sentinel_service import SentinelService, get_sentinel_service
from roboco.services.task import SENTINEL_ITEM_SOURCE, SENTINEL_SOURCE
from sqlalchemy import delete, select, update

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
AUDITOR_UUID = _foundation.AGENTS["auditor"].uuid
CEO_UUID = _foundation.AGENTS["ceo"].uuid
MAIN_PM_UUID = _foundation.AGENTS["main-pm"].uuid
ROBOCO_SLUG = "roboco-standin"
ONE = 1


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
            TaskTable.source == SENTINEL_SOURCE,
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


def _item(idx: int, *, area: str = "waivers", status: str | None = "proposed") -> dict:
    item: dict[str, Any] = {
        "id": f"item-{idx}",
        "area": area,
        "observation": f"Minor findings keep getting waived, item {idx}",
        "evidence": f"{idx + 3} waived-minor findings this week",
        "suggested_action": f"Convert item {idx} to a Pest Control bug task",
    }
    if status is not None:
        item["status"] = status
        item["reject_reason"] = None
        item["materialized_task_id"] = None
    return item


async def _seed_agents(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (AUDITOR_UUID, "auditor", AgentRole.AUDITOR, Team.BOARD),
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


async def _seed_roboco_project(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> ProjectTable:
    await _seed_agents(session)
    project = ProjectTable(
        id=uuid4(),
        name="RoboCo",
        slug=ROBOCO_SLUG,
        git_url="https://example.com/roboco.git",
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
    )
    session.add(project)
    await session.flush()
    monkeypatch.setattr(cfg, "self_heal_project_slug", ROBOCO_SLUG)
    return project


async def _seed_report(
    session: AsyncSession, *, items: list[dict] | None = None
) -> TaskTable:
    await _seed_agents(session)
    task = TaskTable(
        id=uuid4(),
        title="Sentinel drift-watch cycle",
        description="Assess org-wide quality drift and file ONE report.",
        acceptance_criteria=["propose_quality_report() called once"],
        status=TS.COMPLETED,
        priority=2,
        task_type=TT.ADMINISTRATIVE,
        nature=TN.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=SYSTEM_UUID,
        assigned_to=AUDITOR_UUID,
        team=Team.BOARD,
        source=SENTINEL_SOURCE,
        confirmed_by_human=False,
    )
    session.add(task)
    await session.flush()
    items = items or [_item(0), _item(1)]
    markers.set_quality_report(
        task,
        {
            "headline": "Waived findings climbed sharply this week",
            "items": items,
            "overall_assessment": "Drift is concentrated, not systemic",
        },
    )
    await session.flush()
    return task


def _svc(session: AsyncSession) -> SentinelService:
    return get_sentinel_service(session)


def _id(task: TaskTable) -> UUID:
    return cast("UUID", task.id)


@pytest.mark.asyncio
async def test_approve_materializes_main_pm_owned_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_report(db_session)
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
    assert materialized.source == SENTINEL_ITEM_SOURCE
    # team is forced to Team.MAIN_PM — see test_roadmap_service.py's
    # identical assertion for why: every "is this a coordination root"
    # consumer keys on team, not assigned_to. A process/quality drift item
    # has no natural owning cell (the prior Team.BACKEND was an arbitrary
    # placeholder, not a real delegation hint), so there is no cell to
    # preserve in Notes here.
    assert materialized.team == Team.MAIN_PM
    # main_pm can never own a code task (pm_cannot_own_code) — the intake
    # coercion in create_task_from_draft retypes it to planning, the same
    # shape a Main-PM coordination root always carries.
    assert materialized.task_type == TT.PLANNING

    materialized.branch_name = "feature/main_pm/deadbeef"
    hint = _next_hint_pr_fail(materialized)
    assert "re-delegate" in hint
    assert "do NOT re-submit" in hint

    await db_session.refresh(task)
    payload = markers.get_quality_report(task)
    assert payload is not None
    item0 = next(i for i in payload["items"] if i["id"] == "item-0")
    assert item0["status"] == "approved"
    assert item0["materialized_task_id"] == result.materialized_task_id
    assert task.status == TS.COMPLETED


@pytest.mark.asyncio
async def test_docs_area_materializes_documentation_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_report(db_session, items=[_item(0, area="docs")])
    result = await _svc(db_session).approve_item(
        _id(task), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    materialized = await db_session.get(TaskTable, result.materialized_task_id)
    assert materialized is not None
    assert materialized.task_type == TT.DOCUMENTATION


@pytest.mark.asyncio
async def test_approve_is_idempotent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_report(db_session)
    svc = _svc(db_session)
    first = await svc.approve_item(_id(task), "item-0", created_by=CEO_UUID)
    second = await svc.approve_item(_id(task), "item-0", created_by=CEO_UUID)
    assert first is not None
    assert second is not None
    assert second.status == "already_approved"
    assert second.materialized_task_id == first.materialized_task_id

    result = await db_session.execute(
        select(TaskTable).where(
            TaskTable.source == SENTINEL_ITEM_SOURCE,
            TaskTable.title.like("Sentinel [waivers]:%"),
        )
    )
    assert len(result.scalars().all()) == ONE


@pytest.mark.asyncio
async def test_reject_records_reason(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_report(db_session)
    result = await _svc(db_session).reject_item(
        _id(task), "item-0", "already tracked elsewhere"
    )
    assert result is not None
    assert result.status == "rejected"

    await db_session.refresh(task)
    payload = markers.get_quality_report(task)
    assert payload is not None
    item0 = next(i for i in payload["items"] if i["id"] == "item-0")
    assert item0["status"] == "rejected"
    assert item0["reject_reason"] == "already tracked elsewhere"


@pytest.mark.asyncio
async def test_reject_is_idempotent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_report(db_session)
    svc = _svc(db_session)
    await svc.reject_item(_id(task), "item-0", "reason one")
    second = await svc.reject_item(_id(task), "item-0", "reason two")
    assert second is not None
    assert second.status == "already_rejected"


@pytest.mark.asyncio
async def test_cannot_reject_an_approved_item(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_report(db_session)
    svc = _svc(db_session)
    await svc.approve_item(_id(task), "item-0", created_by=CEO_UUID)
    result = await svc.reject_item(_id(task), "item-0", "changed my mind")
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_cannot_approve_a_rejected_item(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_report(db_session)
    svc = _svc(db_session)
    await svc.reject_item(_id(task), "item-0", "not now")
    result = await svc.approve_item(_id(task), "item-0", created_by=CEO_UUID)
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_item_with_no_status_key_defaults_to_proposed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    legacy_item = _item(0, status=None)
    assert "status" not in legacy_item
    task = await _seed_report(db_session, items=[legacy_item])
    result = await _svc(db_session).approve_item(
        _id(task), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"


@pytest.mark.asyncio
async def test_approve_unresolvable_project_is_invalid_state(
    db_session: AsyncSession,
) -> None:
    task = await _seed_report(db_session)
    result = await _svc(db_session).approve_item(
        _id(task), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "invalid_state"
    assert "not resolvable" in result.detail


@pytest.mark.asyncio
async def test_unknown_task_returns_none(db_session: AsyncSession) -> None:
    result = await _svc(db_session).approve_item(uuid4(), "item-0", created_by=CEO_UUID)
    assert result is None


@pytest.mark.asyncio
async def test_unknown_item_id_returns_none(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_report(db_session)
    result = await _svc(db_session).approve_item(
        _id(task), "item-999", created_by=CEO_UUID
    )
    assert result is None


async def _seed_cycle_ledger_row(session: AsyncSession, task: TaskTable) -> None:
    session.add(
        BoardProgramCycleTable(
            program_key="sentinel",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_approve_records_learn_decision(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_report(db_session)
    await _seed_cycle_ledger_row(db_session, task)
    await _svc(db_session).approve_item(_id(task), "item-0", created_by=CEO_UUID)

    row = (
        await db_session.execute(
            select(BoardProgramCycleTable).where(
                BoardProgramCycleTable.program_key == "sentinel"
            )
        )
    ).scalar_one()
    assert row.items_approved == ONE
    decision = row.decisions[0]
    assert decision["verdict"] == "approved"
    assert decision["item_ref"] == "Convert item 0 to a Pest Control bug task"


@pytest.mark.asyncio
async def test_approve_survives_learn_recording_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_report(db_session)
    await _seed_cycle_ledger_row(db_session, task)

    async def _boom(_self: object, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("learn boom")

    monkeypatch.setattr(bp_module.BoardProgramEngine, "record_decision", _boom)
    result = await _svc(db_session).approve_item(
        _id(task), "item-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"

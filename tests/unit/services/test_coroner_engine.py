"""Coroner engine: EVENT-triggered autopsy origination, deduped, never
authors content itself, and (unlike Pest Control) org-scoped — no
per-project opt-in gate. Mirrors test_pest_control_engine.py's shape.
"""

from __future__ import annotations

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
    TaskReviewFindingTable,
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import AgentRole, AgentStatus, Complexity, Team
from roboco.models.base import TaskNature as TN
from roboco.models.base import TaskStatus as TS
from roboco.models.base import TaskType as TT
from roboco.services.coroner_engine import CoronerEngine
from roboco.services.task import (
    CORONER_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    TaskCreateRequest,
    get_task_service,
)
from sqlalchemy import delete, select, update

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
AUDITOR_UUID = _foundation.AGENTS["auditor"].uuid
SLUG = "backend-svc"
ONE = 1


@pytest_asyncio.fixture(autouse=True)
async def _purge_board_program_pollution(db_session: AsyncSession) -> None:
    """See test_board_program_engine.py's identical fixture: Board Program
    settings-store rows / ledger rows / open exploration tasks are shared,
    cross-test-persistent DB state. Purge before every test in this file."""
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
                ]
            ),
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


async def _seed(session: AsyncSession) -> ProjectTable:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (AUDITOR_UUID, "auditor", AgentRole.AUDITOR, Team.BOARD),
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
        name="Backend Service",
        slug=SLUG,
        git_url="https://github.com/x/backend-svc.git",
        default_branch="master",
        protected_branches=["master"],
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        is_active=True,
    )
    session.add(project)
    await session.flush()
    return project


async def _incident(session: AsyncSession, project: ProjectTable) -> TaskTable:
    """A raw delivery task standing in for a bounced/cancelled/budget-blocked
    incident — created PENDING, then raw-set to a terminal-ish status (never
    routed through the transition validator, mirroring test_board_program_
    engine.py's ``_make_exploration`` helper), since CoronerEngine only reads
    the task, never transitions it."""
    task = await get_task_service(session).create(
        TaskCreateRequest(
            title="Chronic task",
            description="Bounced repeatedly",
            acceptance_criteria=["it works"],
            team=Team.BACKEND,
            created_by=SYSTEM_UUID,
            task_type=TT.CODE,
            nature=TN.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            project_id=cast("UUID", project.id),
            status=TS.PENDING,
        )
    )
    task.status = TS.NEEDS_REVISION
    task.revision_count = 3
    await session.flush()
    return task


def _arm(session: AsyncSession) -> None:
    session.add(SystemSettingTable(key="board_program.coroner.enabled", value="true"))


@pytest.mark.asyncio
async def test_disabled_creates_no_cycle(db_session: AsyncSession) -> None:
    project = await _seed(db_session)
    incident = await _incident(db_session, project)
    await db_session.flush()
    engine = CoronerEngine(db_session)
    assert (
        await engine.open_for_incident(cast("UUID", incident.id), kind="bounced")
        is None
    )
    assert await get_task_service(db_session).list_open_coroner_cycles() == []


@pytest.mark.asyncio
async def test_enabled_originates_held_autopsy_task(db_session: AsyncSession) -> None:
    project = await _seed(db_session)
    incident = await _incident(db_session, project)
    _arm(db_session)
    await db_session.flush()
    engine = CoronerEngine(db_session)
    task = await engine.open_for_incident(cast("UUID", incident.id), kind="bounced")
    assert task is not None
    assert task.source == CORONER_SOURCE
    assert task.status == TS.PENDING
    assert task.assigned_to == AUDITOR_UUID
    assert task.confirmed_by_human is False
    ref = markers.get_coroner_incident(task)
    assert ref is not None
    assert ref["incident_task_id"] == str(incident.id)
    assert ref["kind"] == "bounced"

    rows = (await db_session.execute(select(BoardProgramCycleTable))).scalars().all()
    assert len(rows) == ONE
    assert rows[0].program_key == "coroner"
    assert rows[0].exploration_task_id == task.id


@pytest.mark.asyncio
async def test_open_autopsy_blocks_a_second_incident(db_session: AsyncSession) -> None:
    project = await _seed(db_session)
    first_incident = await _incident(db_session, project)
    second_incident = await _incident(db_session, project)
    _arm(db_session)
    await db_session.flush()
    engine = CoronerEngine(db_session)
    first_task = await engine.open_for_incident(
        cast("UUID", first_incident.id), kind="bounced"
    )
    assert first_task is not None

    second_task = await engine.open_for_incident(
        cast("UUID", second_incident.id), kind="cancelled"
    )
    assert second_task is None
    cycles = await get_task_service(db_session).list_open_coroner_cycles()
    assert len(cycles) == ONE


@pytest.mark.asyncio
async def test_unresolvable_incident_returns_none(db_session: AsyncSession) -> None:
    await _seed(db_session)
    _arm(db_session)
    await db_session.flush()
    engine = CoronerEngine(db_session)
    assert await engine.open_for_incident(uuid4(), kind="bounced") is None


@pytest.mark.asyncio
async def test_incident_context_renders_findings_and_transitions(
    db_session: AsyncSession,
) -> None:
    project = await _seed(db_session)
    incident = await _incident(db_session, project)
    await db_session.flush()
    db_session.add(
        TaskReviewFindingTable(
            task_id=incident.id,
            origin="qa",
            round=1,
            author_slug="be-qa",
            file="roboco/app.py",
            line=42,
            severity="blocker",
            criterion="AC 1",
            expected="handles the edge case",
            actual="raises",
            fix="add a guard",
            evidence="repro steps",
        )
    )
    db_session.add(
        AuditLogTable(
            event_type="task.needs_revision",
            target_type="task",
            target_id=incident.id,
            severity="info",
            details={},
        )
    )
    await db_session.flush()

    engine = CoronerEngine(db_session)
    context = await engine.incident_context(cast("UUID", incident.id))
    assert "roboco/app.py:42" in context
    assert "AC 1" in context
    assert "task.needs_revision" in context


@pytest.mark.asyncio
async def test_incident_context_empty_with_no_history(db_session: AsyncSession) -> None:
    project = await _seed(db_session)
    incident = await _incident(db_session, project)
    await db_session.flush()
    engine = CoronerEngine(db_session)
    assert await engine.incident_context(cast("UUID", incident.id)) == ""


@pytest.mark.asyncio
async def test_complete_with_postmortem_completes_the_task(
    db_session: AsyncSession,
) -> None:
    project = await _seed(db_session)
    incident = await _incident(db_session, project)
    _arm(db_session)
    await db_session.flush()
    engine = CoronerEngine(db_session)
    task = await engine.open_for_incident(cast("UUID", incident.id), kind="bounced")
    assert task is not None

    payload = {
        "incident_summary": "it bounced repeatedly",
        "root_cause": "no venv-freshness check",
        "failed_stage": "awaiting_qa",
        "process_change": {"kind": "conventions_rule", "description": "add a check"},
        "playbook_id": None,
    }
    await engine.complete_with_postmortem(task, payload)
    assert task.status == TS.COMPLETED
    assert markers.get_coroner_postmortem(task) == payload

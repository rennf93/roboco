"""CoronerService coverage: approve materializes the postmortem's ONE
process change as a Main-PM-owned root task (idempotent), reject records a
reason (idempotent). Unlike Periscope/Sentinel there is no item id — a
postmortem is one process change, not a list — and the target project
resolves against the INCIDENT task's own project (falling back to RoboCo's),
not a per-item project_slug.
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
from roboco.services.coroner_service import CoronerService, get_coroner_service
from roboco.services.task import CORONER_ITEM_SOURCE, CORONER_SOURCE
from sqlalchemy import delete, select, update

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
AUDITOR_UUID = _foundation.AGENTS["auditor"].uuid
CEO_UUID = _foundation.AGENTS["ceo"].uuid
MAIN_PM_UUID = _foundation.AGENTS["main-pm"].uuid
ROBOCO_SLUG = "roboco-standin"
INCIDENT_SLUG = "customer-app"
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
            TaskTable.source == CORONER_SOURCE,
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


def _process_change(
    *, kind: str = "conventions_rule", status: str | None = "proposed"
) -> dict:
    change: dict[str, Any] = {
        "kind": kind,
        "description": "Add a venv-freshness check to make quality",
    }
    if status is not None:
        change["status"] = status
        change["reject_reason"] = None
        change["materialized_task_id"] = None
    return change


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


async def _seed_incident(session: AsyncSession, *, project: ProjectTable) -> TaskTable:
    """A real incident task on its OWN project/team — distinct from any
    RoboCo fallback project, so a test asserting the incident's project/team
    wins can't accidentally pass via the fallback instead."""
    await _seed_agents(session)
    incident = TaskTable(
        id=uuid4(),
        title="Fix worktree venv rot",
        description="x",
        acceptance_criteria=["x"],
        status=TS.NEEDS_REVISION,
        priority=2,
        task_type=TT.CODE,
        nature=TN.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        created_by=SYSTEM_UUID,
        team=Team.FRONTEND,
        source="manual",
        confirmed_by_human=True,
        project_id=project.id,
        revision_count=3,
    )
    session.add(incident)
    await session.flush()
    return incident


async def _seed_incident_project(session: AsyncSession) -> ProjectTable:
    await _seed_agents(session)
    project = ProjectTable(
        id=uuid4(),
        name="Customer App",
        slug=INCIDENT_SLUG,
        git_url="https://example.com/customer-app.git",
        assigned_cell=Team.FRONTEND,
        created_by=SYSTEM_UUID,
    )
    session.add(project)
    await session.flush()
    return project


async def _seed_postmortem(
    session: AsyncSession,
    *,
    incident: TaskTable | None,
    process_change: dict | None = None,
    postmortem_project_id: object | None = None,
) -> TaskTable:
    await _seed_agents(session)
    task = TaskTable(
        id=uuid4(),
        title="Coroner postmortem",
        description="Autopsy the chronic task.",
        acceptance_criteria=["propose_postmortem() called once"],
        status=TS.COMPLETED,
        priority=2,
        task_type=TT.ADMINISTRATIVE,
        nature=TN.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=SYSTEM_UUID,
        assigned_to=AUDITOR_UUID,
        team=Team.BOARD,
        source=CORONER_SOURCE,
        confirmed_by_human=False,
        project_id=postmortem_project_id,
    )
    session.add(task)
    await session.flush()
    if incident is not None:
        markers.set_coroner_incident(
            task,
            {
                "incident_task_id": str(incident.id),
                "kind": "bounced",
                "revision_count": incident.revision_count or 0,
                "title": incident.title,
            },
        )
    markers.set_coroner_postmortem(
        task,
        {
            "incident_summary": "the task bounced 3 times over a stale venv",
            "root_cause": "the gate never verified the venv's dev extras",
            "failed_stage": "awaiting_qa",
            "process_change": process_change or _process_change(),
            "playbook_id": None,
        },
    )
    await session.flush()
    return task


def _svc(session: AsyncSession) -> CoronerService:
    return get_coroner_service(session)


def _id(task: TaskTable) -> UUID:
    return cast("UUID", task.id)


@pytest.mark.asyncio
async def test_approve_materializes_main_pm_owned_task_on_incident_project(
    db_session: AsyncSession,
) -> None:
    """The target project/team is the INCIDENT's own — not the postmortem
    task's own project_id (left None here) and not a RoboCo fallback."""
    incident_project = await _seed_incident_project(db_session)
    incident = await _seed_incident(db_session, project=incident_project)
    task = await _seed_postmortem(db_session, incident=incident)

    result = await _svc(db_session).approve_process_change(
        _id(task), created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"
    assert result.materialized_task_id is not None

    materialized = await db_session.get(TaskTable, result.materialized_task_id)
    assert materialized is not None
    assert materialized.status == TS.PENDING
    assert materialized.assigned_to == MAIN_PM_UUID
    assert materialized.parent_task_id is None
    assert materialized.source == CORONER_ITEM_SOURCE
    assert materialized.project_id == incident_project.id
    # team is forced to Team.MAIN_PM (not the incident's own cell) — see
    # test_roadmap_service.py's identical assertion for why: every "is this
    # a coordination root" consumer keys on team, not assigned_to.
    assert materialized.team == Team.MAIN_PM
    # main_pm can never own a code task — see test_roadmap_service.py's
    # identical assertion for the coercion rationale.
    assert materialized.task_type == TT.PLANNING
    # The incident's own cell (Team.FRONTEND) survives as a Notes delegation
    # hint instead of the materialized task's team column.
    assert "frontend cell" in (materialized.description or "")

    materialized.branch_name = "feature/main_pm/deadbeef"
    hint = _next_hint_pr_fail(materialized)
    assert "re-delegate" in hint
    assert "do NOT re-submit" in hint

    await db_session.refresh(task)
    payload = markers.get_coroner_postmortem(task)
    assert payload is not None
    assert payload["process_change"]["status"] == "approved"
    assert payload["process_change"]["materialized_task_id"] == str(
        result.materialized_task_id
    )
    assert task.status == TS.COMPLETED


@pytest.mark.asyncio
async def test_approve_falls_back_to_roboco_project_when_incident_gone(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    roboco_project = await _seed_roboco_project(db_session, monkeypatch)
    # coroner_incident references an incident id that no longer resolves.
    task = await _seed_postmortem(db_session, incident=None)
    markers.set_coroner_incident(
        task,
        {
            "incident_task_id": str(uuid4()),
            "kind": "cancelled",
            "revision_count": 0,
            "title": "gone",
        },
    )
    await db_session.flush()

    result = await _svc(db_session).approve_process_change(
        _id(task), created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"
    materialized = await db_session.get(TaskTable, result.materialized_task_id)
    assert materialized is not None
    assert materialized.project_id == roboco_project.id
    # team is forced to Team.MAIN_PM regardless of the fallback team
    # (Team.BACKEND) _resolve_target reports when the incident is gone.
    assert materialized.team == Team.MAIN_PM
    assert "backend cell" in (materialized.description or "")


@pytest.mark.asyncio
async def test_approve_is_idempotent(db_session: AsyncSession) -> None:
    incident_project = await _seed_incident_project(db_session)
    incident = await _seed_incident(db_session, project=incident_project)
    task = await _seed_postmortem(db_session, incident=incident)
    svc = _svc(db_session)
    first = await svc.approve_process_change(_id(task), created_by=CEO_UUID)
    second = await svc.approve_process_change(_id(task), created_by=CEO_UUID)
    assert first is not None
    assert second is not None
    assert second.status == "already_approved"
    assert second.materialized_task_id == first.materialized_task_id

    result = await db_session.execute(
        select(TaskTable).where(TaskTable.source == CORONER_ITEM_SOURCE)
    )
    assert len(result.scalars().all()) == ONE


@pytest.mark.asyncio
async def test_reject_records_reason(db_session: AsyncSession) -> None:
    incident_project = await _seed_incident_project(db_session)
    incident = await _seed_incident(db_session, project=incident_project)
    task = await _seed_postmortem(db_session, incident=incident)
    result = await _svc(db_session).reject_process_change(
        _id(task), "one-off incident, not worth a standing rule"
    )
    assert result is not None
    assert result.status == "rejected"

    await db_session.refresh(task)
    payload = markers.get_coroner_postmortem(task)
    assert payload is not None
    assert payload["process_change"]["status"] == "rejected"
    assert (
        payload["process_change"]["reject_reason"]
        == "one-off incident, not worth a standing rule"
    )


@pytest.mark.asyncio
async def test_reject_is_idempotent(db_session: AsyncSession) -> None:
    incident_project = await _seed_incident_project(db_session)
    incident = await _seed_incident(db_session, project=incident_project)
    task = await _seed_postmortem(db_session, incident=incident)
    svc = _svc(db_session)
    await svc.reject_process_change(_id(task), "reason one")
    second = await svc.reject_process_change(_id(task), "reason two")
    assert second is not None
    assert second.status == "already_rejected"


@pytest.mark.asyncio
async def test_cannot_reject_an_approved_change(db_session: AsyncSession) -> None:
    incident_project = await _seed_incident_project(db_session)
    incident = await _seed_incident(db_session, project=incident_project)
    task = await _seed_postmortem(db_session, incident=incident)
    svc = _svc(db_session)
    await svc.approve_process_change(_id(task), created_by=CEO_UUID)
    result = await svc.reject_process_change(_id(task), "changed my mind")
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_cannot_approve_a_rejected_change(db_session: AsyncSession) -> None:
    incident_project = await _seed_incident_project(db_session)
    incident = await _seed_incident(db_session, project=incident_project)
    task = await _seed_postmortem(db_session, incident=incident)
    svc = _svc(db_session)
    await svc.reject_process_change(_id(task), "not now")
    result = await svc.approve_process_change(_id(task), created_by=CEO_UUID)
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_playbook_kind_refuses_approve(db_session: AsyncSession) -> None:
    incident_project = await _seed_incident_project(db_session)
    incident = await _seed_incident(db_session, project=incident_project)
    task = await _seed_postmortem(
        db_session,
        incident=incident,
        process_change=_process_change(kind="playbook", status="not_applicable"),
    )
    result = await _svc(db_session).approve_process_change(
        _id(task), created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "invalid_state"
    assert "already drafted as a playbook" in result.detail


@pytest.mark.asyncio
async def test_playbook_kind_refuses_reject(db_session: AsyncSession) -> None:
    incident_project = await _seed_incident_project(db_session)
    incident = await _seed_incident(db_session, project=incident_project)
    task = await _seed_postmortem(
        db_session,
        incident=incident,
        process_change=_process_change(kind="playbook", status="not_applicable"),
    )
    result = await _svc(db_session).reject_process_change(_id(task), "no thanks")
    assert result is not None
    assert result.status == "invalid_state"
    assert "already drafted as a playbook" in result.detail


@pytest.mark.asyncio
async def test_process_change_with_no_status_key_defaults_to_proposed(
    db_session: AsyncSession,
) -> None:
    incident_project = await _seed_incident_project(db_session)
    incident = await _seed_incident(db_session, project=incident_project)
    legacy_change = _process_change(status=None)
    assert "status" not in legacy_change
    task = await _seed_postmortem(
        db_session, incident=incident, process_change=legacy_change
    )
    result = await _svc(db_session).approve_process_change(
        _id(task), created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"


@pytest.mark.asyncio
async def test_approve_unresolvable_project_is_invalid_state(
    db_session: AsyncSession,
) -> None:
    """Incident gone AND no RoboCo project seeded — fails cleanly instead of
    guessing a project."""
    task = await _seed_postmortem(db_session, incident=None)
    markers.set_coroner_incident(
        task,
        {
            "incident_task_id": str(uuid4()),
            "kind": "cancelled",
            "revision_count": 0,
            "title": "gone",
        },
    )
    await db_session.flush()
    result = await _svc(db_session).approve_process_change(
        _id(task), created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "invalid_state"
    assert "cannot anchor a materialized task" in result.detail


@pytest.mark.asyncio
async def test_unknown_task_returns_none(db_session: AsyncSession) -> None:
    result = await _svc(db_session).approve_process_change(uuid4(), created_by=CEO_UUID)
    assert result is None


async def _seed_cycle_ledger_row(session: AsyncSession, task: TaskTable) -> None:
    session.add(
        BoardProgramCycleTable(
            program_key="coroner",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_approve_records_learn_decision(db_session: AsyncSession) -> None:
    incident_project = await _seed_incident_project(db_session)
    incident = await _seed_incident(db_session, project=incident_project)
    task = await _seed_postmortem(db_session, incident=incident)
    await _seed_cycle_ledger_row(db_session, task)
    await _svc(db_session).approve_process_change(_id(task), created_by=CEO_UUID)

    row = (
        await db_session.execute(
            select(BoardProgramCycleTable).where(
                BoardProgramCycleTable.program_key == "coroner"
            )
        )
    ).scalar_one()
    assert row.items_approved == ONE
    decision = row.decisions[0]
    assert decision["verdict"] == "approved"
    assert decision["item_ref"] == "Add a venv-freshness check to make quality"


@pytest.mark.asyncio
async def test_approve_survives_learn_recording_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_project = await _seed_incident_project(db_session)
    incident = await _seed_incident(db_session, project=incident_project)
    task = await _seed_postmortem(db_session, incident=incident)
    await _seed_cycle_ledger_row(db_session, task)

    async def _boom(_self: object, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("learn boom")

    monkeypatch.setattr(bp_module.BoardProgramEngine, "record_decision", _boom)
    result = await _svc(db_session).approve_process_change(
        _id(task), created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"

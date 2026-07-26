"""PeriscopeService coverage: per-finding approve materializes a Main-PM-
owned root task (idempotent), reject records a reason (idempotent). Unlike
RoadmapService the exploration task is ALREADY COMPLETED (complete-at-
propose) — there is no cycle-completion transition to test, only the
finding's own status.

Mirrors test_roadmap_service.py's per-item shape, adapted for: no
project_slug on the item (resolves against the RoboCo project instead), and
a finding authored before this feature shipped carrying no status key at all
(setdefault, not a hard requirement).
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
from roboco.services.periscope_service import PeriscopeService, get_periscope_service
from roboco.services.task import PERISCOPE_ITEM_SOURCE, PERISCOPE_SOURCE
from sqlalchemy import delete, select, update

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
HOM_UUID = _foundation.AGENTS["head-marketing"].uuid
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
            TaskTable.source == PERISCOPE_SOURCE,
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


def _finding(idx: int, *, status: str | None = "proposed") -> dict:
    finding: dict[str, Any] = {
        "id": f"finding-{idx}",
        "claim": f"Competitor {idx} shipped an autonomous review agent",
        "source_url": f"https://example.com/competitor-{idx}",
        "relevance": f"Overlaps our pr_reviewer role, finding {idx}",
    }
    if status is not None:
        finding["status"] = status
        finding["reject_reason"] = None
        finding["materialized_task_id"] = None
    return finding


async def _seed_agents(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (HOM_UUID, "head-marketing", AgentRole.HEAD_MARKETING, Team.BOARD),
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
    """The org's own project — the RoboCo project resolution anchor every
    Periscope/Sentinel/Coroner materialization falls back to. Mirrors
    test_periscope_engine.py's ``_seed``/``_arm`` shape."""
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


async def _seed_brief(
    session: AsyncSession, *, findings: list[dict] | None = None
) -> TaskTable:
    await _seed_agents(session)
    task = TaskTable(
        id=uuid4(),
        title="Periscope market-research cycle",
        description="Research the market and file ONE brief.",
        acceptance_criteria=["propose_market_brief() called once"],
        status=TS.COMPLETED,
        priority=2,
        task_type=TT.ADMINISTRATIVE,
        nature=TN.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=SYSTEM_UUID,
        assigned_to=HOM_UUID,
        team=Team.BOARD,
        source=PERISCOPE_SOURCE,
        confirmed_by_human=False,
    )
    session.add(task)
    await session.flush()
    findings = findings or [_finding(0), _finding(1)]
    markers.set_market_brief(
        task,
        {
            "headline": "A rival tool shipped agentic PR review",
            "findings": findings,
            "threats": [],
            "opportunities": [],
            "positioning_note": "",
            "injection_hits": [],
        },
    )
    await session.flush()
    return task


def _svc(session: AsyncSession) -> PeriscopeService:
    return get_periscope_service(session)


def _id(task: TaskTable) -> UUID:
    return cast("UUID", task.id)


@pytest.mark.asyncio
async def test_approve_materializes_main_pm_owned_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_brief(db_session)
    result = await _svc(db_session).approve_finding(
        _id(task), "finding-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"
    assert result.materialized_task_id is not None

    materialized = await db_session.get(TaskTable, result.materialized_task_id)
    assert materialized is not None
    assert materialized.status == TS.PENDING
    assert materialized.assigned_to == MAIN_PM_UUID
    assert materialized.parent_task_id is None
    assert materialized.source == PERISCOPE_ITEM_SOURCE
    # team is forced to Team.MAIN_PM — see test_roadmap_service.py's
    # identical assertion for why: every "is this a coordination root"
    # consumer keys on team, not assigned_to. A market signal has no natural
    # owning cell (the prior Team.BACKEND was an arbitrary placeholder, not
    # a real delegation hint), so there is no cell to preserve in Notes here.
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
    payload = markers.get_market_brief(task)
    assert payload is not None
    finding0 = next(f for f in payload["findings"] if f["id"] == "finding-0")
    assert finding0["status"] == "approved"
    assert finding0["materialized_task_id"] == result.materialized_task_id
    # The exploration task itself stays COMPLETED — approving a finding is
    # orthogonal to the (already terminal) cycle.
    assert task.status == TS.COMPLETED


@pytest.mark.asyncio
async def test_approve_is_idempotent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_brief(db_session)
    svc = _svc(db_session)
    first = await svc.approve_finding(_id(task), "finding-0", created_by=CEO_UUID)
    second = await svc.approve_finding(_id(task), "finding-0", created_by=CEO_UUID)
    assert first is not None
    assert second is not None
    assert second.status == "already_approved"
    assert second.materialized_task_id == first.materialized_task_id

    result = await db_session.execute(
        select(TaskTable).where(
            TaskTable.source == PERISCOPE_ITEM_SOURCE,
            TaskTable.title.like("Market signal:%"),
        )
    )
    assert len(result.scalars().all()) == ONE


@pytest.mark.asyncio
async def test_reject_records_reason(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_brief(db_session)
    result = await _svc(db_session).reject_finding(
        _id(task), "finding-0", "not actionable this quarter"
    )
    assert result is not None
    assert result.status == "rejected"

    await db_session.refresh(task)
    payload = markers.get_market_brief(task)
    assert payload is not None
    finding0 = next(f for f in payload["findings"] if f["id"] == "finding-0")
    assert finding0["status"] == "rejected"
    assert finding0["reject_reason"] == "not actionable this quarter"


@pytest.mark.asyncio
async def test_reject_is_idempotent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_brief(db_session)
    svc = _svc(db_session)
    await svc.reject_finding(_id(task), "finding-0", "reason one")
    second = await svc.reject_finding(_id(task), "finding-0", "reason two")
    assert second is not None
    assert second.status == "already_rejected"


@pytest.mark.asyncio
async def test_cannot_reject_an_approved_finding(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_brief(db_session)
    svc = _svc(db_session)
    await svc.approve_finding(_id(task), "finding-0", created_by=CEO_UUID)
    result = await svc.reject_finding(_id(task), "finding-0", "changed my mind")
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_cannot_approve_a_rejected_finding(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_brief(db_session)
    svc = _svc(db_session)
    await svc.reject_finding(_id(task), "finding-0", "not now")
    result = await svc.approve_finding(_id(task), "finding-0", created_by=CEO_UUID)
    assert result is not None
    assert result.status == "invalid_state"


@pytest.mark.asyncio
async def test_finding_with_no_status_key_defaults_to_proposed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finding authored before this feature shipped carries no status key
    at all — setdefault treats it as proposed, not a crash."""
    await _seed_roboco_project(db_session, monkeypatch)
    legacy_finding = _finding(0, status=None)
    assert "status" not in legacy_finding
    task = await _seed_brief(db_session, findings=[legacy_finding])
    result = await _svc(db_session).approve_finding(
        _id(task), "finding-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"


@pytest.mark.asyncio
async def test_approve_unresolvable_project_is_invalid_state(
    db_session: AsyncSession,
) -> None:
    """No RoboCo project seeded (and no monkeypatched slug pointing at one)
    — the materialize fails cleanly instead of guessing a project."""
    task = await _seed_brief(db_session)
    result = await _svc(db_session).approve_finding(
        _id(task), "finding-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "invalid_state"
    assert "not resolvable" in result.detail


@pytest.mark.asyncio
async def test_unknown_task_returns_none(db_session: AsyncSession) -> None:
    result = await _svc(db_session).approve_finding(
        uuid4(), "finding-0", created_by=CEO_UUID
    )
    assert result is None


@pytest.mark.asyncio
async def test_unknown_finding_id_returns_none(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_brief(db_session)
    result = await _svc(db_session).approve_finding(
        _id(task), "finding-999", created_by=CEO_UUID
    )
    assert result is None


async def _seed_cycle_ledger_row(session: AsyncSession, task: TaskTable) -> None:
    session.add(
        BoardProgramCycleTable(
            program_key="periscope",
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
    task = await _seed_brief(db_session)
    await _seed_cycle_ledger_row(db_session, task)
    await _svc(db_session).approve_finding(_id(task), "finding-0", created_by=CEO_UUID)

    row = (
        await db_session.execute(
            select(BoardProgramCycleTable).where(
                BoardProgramCycleTable.program_key == "periscope"
            )
        )
    ).scalar_one()
    assert row.items_approved == ONE
    # The ref is the finding's CLAIM (wrapped as learn_ref's "title" input) —
    # a finding has no "title" field of its own.
    decision = row.decisions[0]
    assert decision["verdict"] == "approved"
    assert decision["item_ref"] == "Competitor 0 shipped an autonomous review agent"


@pytest.mark.asyncio
async def test_approve_survives_learn_recording_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roboco_project(db_session, monkeypatch)
    task = await _seed_brief(db_session)
    await _seed_cycle_ledger_row(db_session, task)

    async def _boom(_self: object, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("learn boom")

    monkeypatch.setattr(bp_module.BoardProgramEngine, "record_decision", _boom)
    result = await _svc(db_session).approve_finding(
        _id(task), "finding-0", created_by=CEO_UUID
    )
    assert result is not None
    assert result.status == "approved"

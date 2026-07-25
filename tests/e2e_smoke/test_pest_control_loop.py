"""Scenario: the Pest Control (Board Program) loop end to end.

Mirrors test_board_program_loop.py, minus the RoboCo-project resolution (this
program is project-scoped): arming pest_control via the settings-store key
AND opting a project in opens ONE held exploration task the delivery
dispatcher's pending-claim filter skips (board-dispatched, not delivery
work); a second tick dedups (no second task/cycle row); and approving a fake
item through the real ``PestControlService`` moves the LEARN ledger's
counters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.foundation import identity as _foundation
from tests.e2e_smoke.arcs import Company, seed_company, seed_project

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack

ONE = 1
ZERO = 0


def _seed_system_and_po(stack: E2EStack) -> None:
    """Seed ``system`` + ``product-owner`` at their FIXED foundation UUIDs —
    see test_board_program_loop.py's identical helper for why."""
    from roboco.db.tables import AgentTable
    from roboco.foundation import identity as _foundation
    from roboco.models import AgentRole, AgentStatus, Team

    async def _run(session: AsyncSession) -> None:
        for agent_uuid, slug, role, team in (
            (_foundation.AGENTS["system"].uuid, "system", AgentRole.SYSTEM, None),
            (
                _foundation.AGENTS["product-owner"].uuid,
                "product-owner",
                AgentRole.PRODUCT_OWNER,
                Team.BOARD,
            ),
        ):
            if await session.get(AgentTable, agent_uuid) is not None:
                continue
            session.add(
                AgentTable(
                    id=agent_uuid,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=team,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt=slug,
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )

    stack.run_db(_run)


def _arm_and_opt_in(stack: E2EStack, project_id: Any) -> None:
    """Arm via the settings-store key (the ONLY arming path — no legacy env
    flag exists for pest_control) AND opt the project into the program."""
    from roboco.db.tables import ProjectTable, SystemSettingTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> None:
        session.add(
            SystemSettingTable(key="board_program.pest_control.enabled", value="true")
        )
        project = (
            await session.execute(
                select(ProjectTable).where(ProjectTable.id == project_id)
            )
        ).scalar_one()
        project.board_programs = ["pest_control"]

    stack.run_db(_run)


def _run_due_programs(stack: E2EStack) -> list[str]:
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> list[str]:
        return await get_board_program_engine(session).run_due_programs()

    result: list[str] = stack.run_db(_run)
    return result


def _find_pest_control_task(stack: E2EStack) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from roboco.services.task import PEST_CONTROL_SOURCE
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.source == PEST_CONTROL_SOURCE)
                )
            )
            .scalars()
            .all()
        )
        return {
            "count": len(rows),
            "rows": [
                {
                    "id": r.id,
                    "status": str(r.status),
                    "assigned_to": r.assigned_to,
                    "source": r.source,
                    "confirmed_by_human": r.confirmed_by_human,
                }
                for r in rows
            ],
        }

    state: dict[str, Any] = stack.run_db(_run)
    return state


def _cycle_counters(stack: E2EStack) -> dict[str, Any]:
    from roboco.db.tables import BoardProgramCycleTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        row = (
            (
                await session.execute(
                    select(BoardProgramCycleTable)
                    .where(BoardProgramCycleTable.program_key == "pest_control")
                    .order_by(BoardProgramCycleTable.opened_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        assert row is not None
        return {
            "items_proposed": row.items_proposed,
            "items_approved": row.items_approved,
            "items_rejected": row.items_rejected,
        }

    state: dict[str, Any] = stack.run_db(_run)
    return state


def _approve_fake_item(
    stack: E2EStack, task_id: Any, project_slug: str, company: Company
) -> str:
    from roboco.db.tables import TaskTable
    from roboco.foundation.policy.content import markers
    from roboco.services.pest_control_service import get_pest_control_service
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> str:
        task = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        markers.set_pest_hunt(
            task,
            {
                "items": [
                    {
                        "id": "item-0",
                        "title": "Fix stale worktree venv",
                        "description": ("Fresh worktrees silently reuse a rotted venv"),
                        "acceptance_criteria": ["fresh worktree passes make quality"],
                        "project_slug": project_slug,
                        "team": "backend",
                        "priority": 2,
                        "evidence": (
                            "task_review_findings row F-abc123 waived 4x on this file"
                        ),
                        "status": "proposed",
                        "reject_reason": None,
                        "materialized_task_id": None,
                    }
                ],
            },
        )
        await session.flush()
        result = await get_pest_control_service(session).approve_item(
            task_id, "item-0", created_by=company.ceo_id
        )
        assert result is not None
        return result.status

    status: str = stack.run_db(_run)
    return status


def test_pest_control_loop_originates_dedups_and_records(
    e2e_stack: E2EStack,
) -> None:
    stack = e2e_stack
    company = seed_company(stack)
    _seed_system_and_po(stack)
    project_id, project_slug = seed_project(stack, company)
    _arm_and_opt_in(stack, project_id)

    opened = _run_due_programs(stack)
    assert opened == ["pest_control"], opened

    state = _find_pest_control_task(stack)
    assert state["count"] == ONE, state
    row = state["rows"][0]
    assert row["status"] == "pending"
    assert row["assigned_to"] == _foundation.AGENTS["product-owner"].uuid
    assert row["confirmed_by_human"] is False

    # The dispatcher's own dev-work skip recognizes this exact task shape —
    # board_pest_control is board-dispatched (one-shot PO spawn), never
    # handed to the generic dev dispatch loop's give_me_work/claim path.
    from roboco.runtime.orchestrator import _is_non_dev_dispatch_source

    assert _is_non_dev_dispatch_source({"source": row["source"]}) is True

    # Second tick: the open cycle blocks re-origination — no second task.
    opened_again = _run_due_programs(stack)
    assert opened_again == [], opened_again
    state_after = _find_pest_control_task(stack)
    assert state_after["count"] == ONE, state_after

    # Approve a fake item on the open cycle through the real
    # PestControlService — the LEARN ledger's counters move.
    status = _approve_fake_item(stack, row["id"], project_slug, company)
    assert status == "approved"

    counters = _cycle_counters(stack)
    assert counters["items_proposed"] == ONE
    assert counters["items_approved"] == ONE
    assert counters["items_rejected"] == ZERO

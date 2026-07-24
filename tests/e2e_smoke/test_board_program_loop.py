"""Scenario: the generic Board Program loop end to end (Task 9).

Drives the REAL ``BoardProgramEngine`` (not a mock) against a REAL Postgres
through the harness: arming the roadmap program via the new per-program
settings-store key opens ONE held exploration task the delivery dispatcher's
pending-claim filter skips (it's board-dispatched, not delivery work); a
second tick dedups (no second task/cycle row); and approving a fake item
through the real ``RoadmapService`` moves the LEARN ledger's counters.
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
    """Seed ``system`` + ``product-owner`` at their FIXED foundation UUIDs.

    ``RoadmapEngine._originate`` stamps ``assigned_to``/``created_by`` from
    the static identity registry (not a DB lookup keyed by role), so those
    exact ids must exist as real agent rows for the FK to resolve —
    ``seed_company``'s random ``uuid4()`` agents don't cover this (mirrors
    ``test_feature_spotlight.py``'s ``_seed_system_and_secretary``)."""
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


def _arm_roadmap(stack: E2EStack, project_slug: str) -> None:
    """Arm via the settings-store key ONLY — ``RoadmapEngine.run_cycle`` now
    routes through ``roboco.services.board_programs.program_armed``, the
    same resolver ``BoardProgramEngine.enabled`` consults, so the legacy
    ``roadmap_engine_enabled`` flag stays False here on purpose: this is the
    end-to-end guard against the double-flag regression where the settings
    store alone used to be silently overridden by a False legacy flag."""
    from roboco.config import settings as cfg
    from roboco.db.tables import SystemSettingTable

    cfg.self_heal_project_slug = project_slug

    async def _run(session: AsyncSession) -> None:
        session.add(
            SystemSettingTable(key="board_program.roadmap.enabled", value="true")
        )

    stack.run_db(_run)


def _run_due_programs(stack: E2EStack) -> list[str]:
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> list[str]:
        return await get_board_program_engine(session).run_due_programs()

    result: list[str] = stack.run_db(_run)
    return result


def _find_roadmap_task(stack: E2EStack) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from roboco.services.task import ROADMAP_SOURCE
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.source == ROADMAP_SOURCE)
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
                    .where(BoardProgramCycleTable.program_key == "roadmap")
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
    from roboco.services.roadmap_service import get_roadmap_service
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> str:
        task = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        markers.set_roadmap_cycle(
            task,
            {
                "goal": "Close onboarding friction",
                "items": [
                    {
                        "id": "item-0",
                        "title": "Streamline signup",
                        "description": "Cut the signup form from 8 fields to 3",
                        "acceptance_criteria": ["signup takes < 30s"],
                        "project_slug": project_slug,
                        "team": "backend",
                        "priority": 2,
                        "rationale": "signup drop-off is the top funnel leak",
                        "status": "proposed",
                        "reject_reason": None,
                        "materialized_task_id": None,
                    }
                ],
            },
        )
        await session.flush()
        result = await get_roadmap_service(session).approve_item(
            task_id, "item-0", created_by=company.ceo_id
        )
        assert result is not None
        return result.status

    status: str = stack.run_db(_run)
    return status


def test_board_program_loop_originates_dedups_and_records(
    e2e_stack: E2EStack,
) -> None:
    stack = e2e_stack
    company = seed_company(stack)
    _seed_system_and_po(stack)
    _project_id, project_slug = seed_project(stack, company)
    _arm_roadmap(stack, project_slug)

    opened = _run_due_programs(stack)
    assert opened == ["roadmap"], opened

    state = _find_roadmap_task(stack)
    assert state["count"] == ONE, state
    row = state["rows"][0]
    assert row["status"] == "pending"
    assert row["assigned_to"] == _foundation.AGENTS["product-owner"].uuid
    assert row["confirmed_by_human"] is False

    # The dispatcher's own dev-work skip recognizes this exact task shape —
    # board_roadmap is board-dispatched (one-shot PO spawn), never handed to
    # the generic dev dispatch loop's give_me_work/claim path.
    from roboco.runtime.orchestrator import _is_non_dev_dispatch_source

    assert _is_non_dev_dispatch_source({"source": row["source"]}) is True

    # Second tick: the open cycle blocks re-origination — no second task.
    opened_again = _run_due_programs(stack)
    assert opened_again == [], opened_again
    state_after = _find_roadmap_task(stack)
    assert state_after["count"] == ONE, state_after

    # Approve a fake item on the open cycle through the real RoadmapService —
    # the LEARN ledger's counters move.
    status = _approve_fake_item(stack, row["id"], project_slug, company)
    assert status == "approved"

    counters = _cycle_counters(stack)
    assert counters["items_proposed"] == ONE
    assert counters["items_approved"] == ONE
    assert counters["items_rejected"] == ZERO

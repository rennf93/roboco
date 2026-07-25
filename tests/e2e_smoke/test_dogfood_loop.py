"""Scenario: the Dogfood (Board Program) loop end to end.

Unlike test_spackle_loop.py (a CRON program driven by ``run_due_programs``),
Dogfood is EVENT-triggered — both its triggers (a release-publish hook and a
CEO "run now") route through the SAME ``BoardProgramEngine.open_program_cycle``
call, which this test drives directly (mirrors what
``ReleaseProposalService._draft_dogfood_walk`` and the panel's "run now"
button both do). Arming dogfood via the settings-store key AND opting a
project in opens ONE held exploration task the delivery dispatcher's
pending-claim filter skips (board-dispatched, not delivery work); a second
call dedups (no second task/cycle row); and approving a fake item through
the real ``DogfoodService`` moves the LEARN ledger's counters.
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
    flag exists for dogfood) AND opt the project into the program."""
    from roboco.db.tables import ProjectTable, SystemSettingTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> None:
        session.add(
            SystemSettingTable(key="board_program.dogfood.enabled", value="true")
        )
        project = (
            await session.execute(
                select(ProjectTable).where(ProjectTable.id == project_id)
            )
        ).scalar_one()
        project.board_programs = ["dogfood"]

    stack.run_db(_run)


def _open_program_cycle(stack: E2EStack, key: str) -> Any:
    """Drives the SAME chokepoint both Dogfood triggers use (the
    release-publish hook and the panel's "run now" button) — see this
    module's docstring."""
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> Any:
        task = await get_board_program_engine(session).open_program_cycle(key)
        return task.id if task is not None else None

    return stack.run_db(_run)


def _find_dogfood_task(stack: E2EStack) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from roboco.services.task import DOGFOOD_SOURCE
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.source == DOGFOOD_SOURCE)
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
                    .where(BoardProgramCycleTable.program_key == "dogfood")
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
    from roboco.services.dogfood_service import get_dogfood_service
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> str:
        task = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        markers.set_friction_fixes(
            task,
            {
                "items": [
                    {
                        "id": "item-0",
                        "title": "Settings save button gives no feedback",
                        "description": (
                            "Clicking Save shows nothing — no toast, no spinner"
                        ),
                        "acceptance_criteria": ["a toast confirms the save"],
                        "project_slug": project_slug,
                        "team": "frontend",
                        "priority": 2,
                        "evidence": (
                            "Walked /settings -> clicked Save -> network tab "
                            "shows 200 OK but no UI feedback at all"
                        ),
                        "status": "proposed",
                        "reject_reason": None,
                        "materialized_task_id": None,
                    }
                ],
            },
        )
        await session.flush()
        result = await get_dogfood_service(session).approve_item(
            task_id, "item-0", created_by=company.ceo_id
        )
        assert result is not None
        return result.status

    status: str = stack.run_db(_run)
    return status


def test_dogfood_loop_originates_dedups_and_records(
    e2e_stack: E2EStack,
) -> None:
    stack = e2e_stack
    company = seed_company(stack)
    _seed_system_and_po(stack)
    project_id, project_slug = seed_project(stack, company)
    _arm_and_opt_in(stack, project_id)

    # "run now" (and the release-publish hook, which calls the identical
    # open_program_cycle path) originates a real cycle — unlike Coroner's
    # never-firing stub, Dogfood has a real project-scoped originator.
    opened_id = _open_program_cycle(stack, "dogfood")
    assert opened_id is not None

    state = _find_dogfood_task(stack)
    assert state["count"] == ONE, state
    row = state["rows"][0]
    assert row["status"] == "pending"
    assert row["assigned_to"] == _foundation.AGENTS["product-owner"].uuid
    assert row["confirmed_by_human"] is False

    # The dispatcher's own dev-work skip recognizes this exact task shape —
    # board_dogfood is board-dispatched (one-shot PO spawn), never handed to
    # the generic dev dispatch loop's give_me_work/claim path.
    from roboco.runtime.orchestrator import _is_non_dev_dispatch_source

    assert _is_non_dev_dispatch_source({"source": row["source"]}) is True

    # Second call: the open cycle blocks re-origination — no second task.
    opened_again = _open_program_cycle(stack, "dogfood")
    assert opened_again is None
    state_after = _find_dogfood_task(stack)
    assert state_after["count"] == ONE, state_after

    # Approve a fake item on the open cycle through the real DogfoodService —
    # the LEARN ledger's counters move.
    status = _approve_fake_item(stack, row["id"], project_slug, company)
    assert status == "approved"

    counters = _cycle_counters(stack)
    assert counters["items_proposed"] == ONE
    assert counters["items_approved"] == ONE
    assert counters["items_rejected"] == ZERO


def test_dogfood_never_opens_via_run_due_programs(e2e_stack: E2EStack) -> None:
    """EVENT programs are opened only through ``open_program_cycle``, never
    the CRON loop — armed + opted-in must still originate nothing when the
    loop tick runs, mirroring test_coroner_loop's event-only posture."""
    stack = e2e_stack
    company = seed_company(stack)
    _seed_system_and_po(stack)
    project_id, _project_slug = seed_project(stack, company)
    _arm_and_opt_in(stack, project_id)

    from roboco.services.board_programs import get_board_program_engine

    async def _run_due(session: AsyncSession) -> list[str]:
        return await get_board_program_engine(session).run_due_programs()

    opened: list[str] = stack.run_db(_run_due)
    assert "dogfood" not in opened

    state = _find_dogfood_task(stack)
    assert state["count"] == ZERO, state

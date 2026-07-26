"""Scenario: the Mirror (Board Program) loop end to end.

Mirrors test_spackle_loop.py's arm -> originate -> dedup -> approve shape,
adapted for the identity collision test_periscope_loop.py's
``_seed_system_and_hom`` already solved: ``tests.e2e_smoke.arcs.
seed_company`` seeds its OWN ``head-marketing`` row under a random
``uuid4()`` (cached once per test session), which collides on the unique
``slug`` column with the FIXED foundation UUID ``MirrorEngine._originate``
hardcodes as ``assigned_to`` — so this test seeds its own system/head-
marketing/ceo agents plus its own opted-in project directly, exactly like
``test_periscope_loop.py``, rather than going through ``arcs.seed_company``/
``arcs.seed_project`` (spackle's shape works unmodified only because
``product-owner`` is absent from ``seed_company``'s roster).

Arming mirror via the settings-store key AND opting a project in opens ONE
held exploration task the delivery dispatcher's pending-claim filter skips
(board-dispatched, not delivery work); a second tick dedups (no second
task/cycle row); and approving a fake item through the real
``MirrorService`` moves the LEARN ledger's counters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from roboco.foundation import identity as _foundation

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack

ONE = 1
ZERO = 0


def _seed_system_hom_ceo_and_project(stack: E2EStack) -> str:
    """Seed ``system`` + ``head-marketing`` + ``ceo`` at their FIXED
    foundation UUIDs, plus a project those two own and that opts into
    mirror — ``MirrorEngine._originate`` writes ``created_by``/
    ``assigned_to`` straight from the static identity registry (not
    ``arcs.seed_company``'s random ``uuid4()`` agents; mirrors
    test_periscope_loop.py's identical helper). Returns the project's slug.
    """
    from roboco.db.tables import AgentTable, ProjectTable
    from roboco.models import AgentRole, AgentStatus, Team

    slug = f"e2e-mirror-{uuid4().hex[:8]}"

    async def _run(session: AsyncSession) -> None:
        for agent_uuid, agent_slug, role, team in (
            (_foundation.AGENTS["system"].uuid, "system", AgentRole.SYSTEM, None),
            (
                _foundation.AGENTS["head-marketing"].uuid,
                "head-marketing",
                AgentRole.HEAD_MARKETING,
                Team.BOARD,
            ),
            (_foundation.AGENTS["ceo"].uuid, "ceo", AgentRole.CEO, None),
            (
                _foundation.AGENTS["main-pm"].uuid,
                "main-pm",
                AgentRole.MAIN_PM,
                Team.MAIN_PM,
            ),
        ):
            if await session.get(AgentTable, agent_uuid) is not None:
                continue
            session.add(
                AgentTable(
                    id=agent_uuid,
                    name=agent_slug,
                    slug=agent_slug,
                    role=role,
                    team=team,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt=agent_slug,
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )
        session.add(
            ProjectTable(
                id=uuid4(),
                name=f"E2E {slug}",
                slug=slug,
                git_url=f"https://example.com/{slug}.git",
                default_branch="master",
                protected_branches=["master"],
                assigned_cell=Team.BACKEND,
                created_by=_foundation.AGENTS["system"].uuid,
                is_active=True,
                board_programs=["mirror"],
            )
        )

    stack.run_db(_run)
    return slug


def _arm(stack: E2EStack) -> None:
    """Arm via the settings-store key — the ONLY arming path (no legacy env
    flag exists for mirror)."""
    from roboco.db.tables import SystemSettingTable

    async def _run(session: AsyncSession) -> None:
        session.add(
            SystemSettingTable(key="board_program.mirror.enabled", value="true")
        )

    stack.run_db(_run)


def _run_due_programs(stack: E2EStack) -> list[str]:
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> list[str]:
        return await get_board_program_engine(session).run_due_programs()

    result: list[str] = stack.run_db(_run)
    return result


def _find_mirror_task(stack: E2EStack) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from roboco.services.task import MIRROR_SOURCE
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.source == MIRROR_SOURCE)
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
                    .where(BoardProgramCycleTable.program_key == "mirror")
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


def _approve_fake_item(stack: E2EStack, task_id: Any, project_slug: str) -> str:
    from roboco.db.tables import TaskTable
    from roboco.foundation.policy.content import markers
    from roboco.services.mirror_service import get_mirror_service
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> str:
        task = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        markers.set_messaging_fixes(
            task,
            {
                "items": [
                    {
                        "id": "item-0",
                        "title": "Fix README real-time sync claim",
                        "description": (
                            "README says real-time; sync is polled every 30s"
                        ),
                        "acceptance_criteria": ["README wording matches behavior"],
                        "project_slug": project_slug,
                        "team": "backend",
                        "priority": 2,
                        "evidence": (
                            "README.md:42 says 'real-time sync'; "
                            "roboco/services/sync.py:88 polls every 30s"
                        ),
                        "status": "proposed",
                        "reject_reason": None,
                        "materialized_task_id": None,
                    }
                ],
            },
        )
        await session.flush()
        result = await get_mirror_service(session).approve_item(
            task_id, "item-0", created_by=_foundation.AGENTS["ceo"].uuid
        )
        assert result is not None
        return result.status

    status: str = stack.run_db(_run)
    return status


def test_mirror_loop_originates_dedups_and_records(
    e2e_stack: E2EStack,
) -> None:
    stack = e2e_stack
    project_slug = _seed_system_hom_ceo_and_project(stack)
    _arm(stack)

    opened = _run_due_programs(stack)
    assert opened == ["mirror"], opened

    state = _find_mirror_task(stack)
    assert state["count"] == ONE, state
    row = state["rows"][0]
    assert row["status"] == "pending"
    assert row["assigned_to"] == _foundation.AGENTS["head-marketing"].uuid
    assert row["confirmed_by_human"] is False

    # The dispatcher's own dev-work skip recognizes this exact task shape —
    # board_mirror is board-dispatched (one-shot HoM spawn), never handed to
    # the generic dev dispatch loop's give_me_work/claim path.
    from roboco.runtime.orchestrator import _is_non_dev_dispatch_source

    assert _is_non_dev_dispatch_source({"source": row["source"]}) is True

    # Second tick: the open cycle blocks re-origination — no second task.
    opened_again = _run_due_programs(stack)
    assert opened_again == [], opened_again
    state_after = _find_mirror_task(stack)
    assert state_after["count"] == ONE, state_after

    # Approve a fake item on the open cycle through the real MirrorService —
    # the LEARN ledger's counters move.
    status = _approve_fake_item(stack, row["id"], project_slug)
    assert status == "approved"

    counters = _cycle_counters(stack)
    assert counters["items_proposed"] == ONE
    assert counters["items_approved"] == ONE
    assert counters["items_rejected"] == ZERO

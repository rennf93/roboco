"""Scenario: the Scales (Board Program) loop end to end.

Mirrors test_pest_control_loop.py's arm -> originate -> dedup -> approve
shape (org-scoped like Periscope: no per-project opt-in is needed to RUN,
but the exploration task's project_id still resolves against the RoboCo
project) AND test_periscope_loop.py's do_server wiring-regression check +
real-route propose call. The genuinely new piece: a rebalance item targets a
LIVE task, and approving it EXECUTES the action (reprioritize or cancel)
against that task — never materializes a new one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from roboco.foundation import identity as _foundation
from tests.e2e_smoke.harness import ScriptedAgent, expect_ok

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack

ONE = 1
ZERO = 0
TWO = 2


def _seed_system_and_po(stack: E2EStack) -> tuple[str, Any]:
    """Seed ``system`` + ``product-owner`` at their FIXED foundation UUIDs,
    plus a project those two own — mirrors test_periscope_loop.py's
    ``_seed_system_and_hom``: the exploration task's ``project_id`` resolves
    against this project (the RoboCo-project FK anchor every org-scoped
    program's task still needs). Returns (project slug, project id).
    """
    from roboco.db.tables import AgentTable, ProjectTable
    from roboco.models import AgentRole, AgentStatus, Team

    slug = f"e2e-scales-{uuid4().hex[:8]}"
    project_id = uuid4()

    async def _run(session: AsyncSession) -> None:
        for agent_uuid, agent_slug, role, team in (
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
                id=project_id,
                name="RoboCo",
                slug=slug,
                git_url="https://example.com/roboco.git",
                default_branch="master",
                protected_branches=["master"],
                assigned_cell=Team.BACKEND,
                created_by=_foundation.AGENTS["system"].uuid,
                is_active=True,
            )
        )

    stack.run_db(_run)
    return slug, project_id


def _seed_target_task(
    stack: E2EStack, project_id: Any, *, title: str, priority: int = 2
) -> Any:
    from roboco.db.tables import TaskTable
    from roboco.models import Team
    from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType

    task_id = uuid4()

    async def _run(session: AsyncSession) -> None:
        session.add(
            TaskTable(
                id=task_id,
                title=title,
                description="A live backlog task the rebalance plan targets",
                acceptance_criteria=["done"],
                status=TaskStatus.BACKLOG,
                priority=priority,
                task_type=TaskType.CODE,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.LOW,
                created_by=_foundation.AGENTS["system"].uuid,
                project_id=project_id,
                team=Team.BACKEND,
            )
        )

    stack.run_db(_run)
    return task_id


def _arm(stack: E2EStack, project_slug: str) -> None:
    """Arm via the settings-store key — the ONLY arming path (no legacy env
    flag exists for scales) — and point ``self_heal_project_slug`` at the
    seeded project (the RoboCo-project resolution roadmap/periscope share)."""
    from roboco.config import settings as cfg
    from roboco.db.tables import SystemSettingTable

    cfg.self_heal_project_slug = project_slug

    async def _run(session: AsyncSession) -> None:
        session.add(
            SystemSettingTable(key="board_program.scales.enabled", value="true")
        )

    stack.run_db(_run)


def _run_due_programs(stack: E2EStack) -> list[str]:
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> list[str]:
        return await get_board_program_engine(session).run_due_programs()

    result: list[str] = stack.run_db(_run)
    return result


def _find_scales_task(stack: E2EStack) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from roboco.services.task import SCALES_SOURCE
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.source == SCALES_SOURCE)
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
                    .where(BoardProgramCycleTable.program_key == "scales")
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


def _approve_item(stack: E2EStack, task_id: Any, item_id: str, ceo_id: Any) -> str:
    from roboco.services.scales_service import get_scales_service

    async def _run(session: AsyncSession) -> str:
        result = await get_scales_service(session).approve_item(
            task_id, item_id, created_by=ceo_id
        )
        assert result is not None
        return result.status

    status: str = stack.run_db(_run)
    return status


def _target_state(stack: E2EStack, task_id: Any) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        return {"status": str(row.status), "priority": row.priority}

    state: dict[str, Any] = stack.run_db(_run)
    return state


def test_scales_loop_originates_dedups_proposes_and_executes(
    e2e_stack: E2EStack,
) -> None:
    stack = e2e_stack
    project_slug, project_id = _seed_system_and_po(stack)
    _arm(stack, project_slug)

    reprioritize_target = _seed_target_task(
        stack, project_id, title="Stale onboarding polish", priority=2
    )
    cancel_target = _seed_target_task(
        stack, project_id, title="Dead-weight experiment", priority=2
    )

    opened = _run_due_programs(stack)
    assert opened == ["scales"], opened

    state = _find_scales_task(stack)
    assert state["count"] == ONE, state
    row = state["rows"][0]
    assert row["status"] == "pending"
    assert row["assigned_to"] == _foundation.AGENTS["product-owner"].uuid
    assert row["confirmed_by_human"] is False

    # The dispatcher's own dev-work skip recognizes this exact task shape —
    # board_scales is board-dispatched (one-shot PO spawn), never handed to
    # the generic dev dispatch loop's give_me_work/claim path.
    from roboco.runtime.orchestrator import _is_non_dev_dispatch_source

    assert _is_non_dev_dispatch_source({"source": row["source"]}) is True

    # Second tick: the open cycle blocks re-origination — no second task.
    opened_again = _run_due_programs(stack)
    assert opened_again == [], opened_again
    state_after = _find_scales_task(stack)
    assert state_after["count"] == ONE, state_after

    # The Product Owner authors the plan through the REAL do_server module +
    # /api/v1/do/propose_rebalance route + ContentActions + SCALES service
    # wiring — the same wiring-regression class check test_periscope_loop.py
    # guards (a verb granted in role_config but missing from do_server._TOOLS
    # is unreachable over MCP).
    po = ScriptedAgent(
        stack,
        _foundation.AGENTS["product-owner"].uuid,
        "product-owner",
        "product_owner",
    )
    do_module = po._module("roboco.mcp.do_server")
    assert "propose_rebalance" in do_module._TOOLS, (
        "propose_rebalance missing from do_server._TOOLS — the MCP server "
        "has no way to expose it to any role"
    )
    assert "propose_rebalance" in do_module._REGISTERED_TOOLS, (
        "propose_rebalance is granted to product_owner in role_config but "
        "absent from this agent's _register_tools() output — the manifest "
        "-> _register_tools -> callable chain dropped it"
    )

    env = expect_ok(
        po.do(
            "propose_rebalance",
            items=[
                {
                    "task_ref": str(reprioritize_target)[:8],
                    "action": "reprioritize",
                    "new_priority": 0,
                    "rationale": (
                        "Onboarding friction is this quarter's top charter goal"
                    ),
                },
                {
                    "task_ref": str(cancel_target)[:8],
                    "action": "cancel",
                    "rationale": (
                        "Superseded by the new dashboard; no longer on the roadmap"
                    ),
                },
            ],
        ),
        "po propose_rebalance",
    )
    assert env.get("status") == "rebalance_proposed", env
    task_id = UUID(env["task_id"])
    assert task_id == row["id"]

    # Approving each item EXECUTES its action against the live target task —
    # neither materializes a new task.
    ceo_id = _foundation.AGENTS["ceo"].uuid
    from roboco.db.tables import AgentTable
    from roboco.models import AgentRole, AgentStatus

    async def _seed_ceo(session: AsyncSession) -> None:
        if await session.get(AgentTable, ceo_id) is not None:
            return
        session.add(
            AgentTable(
                id=ceo_id,
                name="ceo",
                slug="ceo",
                role=AgentRole.CEO,
                team=None,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="ceo",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )

    stack.run_db(_seed_ceo)

    reprioritize_status = _approve_item(stack, task_id, "item-0", ceo_id)
    assert reprioritize_status == "approved"
    cancel_status = _approve_item(stack, task_id, "item-1", ceo_id)
    assert cancel_status == "approved"

    reprioritize_state = _target_state(stack, reprioritize_target)
    assert reprioritize_state["status"] == "backlog"
    assert reprioritize_state["priority"] == 0

    cancel_state = _target_state(stack, cancel_target)
    assert cancel_state["status"] == "cancelled"

    counters = _cycle_counters(stack)
    assert counters["items_proposed"] == TWO
    assert counters["items_approved"] == TWO
    assert counters["items_rejected"] == ZERO

"""Scenario: the Sentinel (Board Program) loop end to end.

Mirrors test_periscope_loop.py's arm -> originate -> dedup shape (same
RoboCo-project resolution — org scope means no per-project opt-in is needed
to RUN, but the exploration task's project_id still resolves against the
RoboCo project, a hard TaskService invariant every non-coordination task
carries) AND its do_server wiring-regression check + real-route propose call.
Like a market brief, a quality report completes the SAME exploration task it
was authored on (no separate materialized draft/item task).
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


def _seed_system_and_auditor(stack: E2EStack) -> str:
    """Seed ``system`` + ``auditor`` at their FIXED foundation UUIDs, plus a
    project those two own — ``SentinelEngine._originate`` writes
    ``created_by``/``assigned_to`` straight from the static identity
    registry (not ``seed_company``'s random ``uuid4()`` agents; mirrors
    test_periscope_loop.py's identical ``_seed_system_and_hom``), and the
    exploration task's ``project_id`` resolves against this project (the
    RoboCo-project FK anchor every org-scoped program's task still needs).
    Returns the project's slug.
    """
    from roboco.db.tables import AgentTable, ProjectTable
    from roboco.models import AgentRole, AgentStatus, Team

    slug = f"e2e-sentinel-{uuid4().hex[:8]}"

    async def _run(session: AsyncSession) -> None:
        for agent_uuid, agent_slug, role, team in (
            (_foundation.AGENTS["system"].uuid, "system", AgentRole.SYSTEM, None),
            (
                _foundation.AGENTS["auditor"].uuid,
                "auditor",
                AgentRole.AUDITOR,
                None,
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
    return slug


def _arm(stack: E2EStack, project_slug: str) -> None:
    """Arm via the settings-store key — the ONLY arming path (no legacy env
    flag exists for sentinel) — and point ``self_heal_project_slug`` at the
    seeded project (the RoboCo-project resolution roadmap/x_feature/periscope
    use)."""
    from roboco.config import settings as cfg
    from roboco.db.tables import SystemSettingTable

    cfg.self_heal_project_slug = project_slug

    async def _run(session: AsyncSession) -> None:
        session.add(
            SystemSettingTable(key="board_program.sentinel.enabled", value="true")
        )

    stack.run_db(_run)


def _run_due_programs(stack: E2EStack) -> list[str]:
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> list[str]:
        return await get_board_program_engine(session).run_due_programs()

    result: list[str] = stack.run_db(_run)
    return result


def _find_sentinel_task(stack: E2EStack) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from roboco.services.task import SENTINEL_SOURCE
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.source == SENTINEL_SOURCE)
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
                    "project_id": r.project_id,
                    "confirmed_by_human": r.confirmed_by_human,
                }
                for r in rows
            ],
        }

    state: dict[str, Any] = stack.run_db(_run)
    return state


def _run_due_one(stack: E2EStack) -> Any:
    """Open a cycle off-schedule (enabled + dedup only, no cron-due check) —
    the strategy-engine trigger / "run now" seam."""
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> Any:
        task = await get_board_program_engine(session).open_program_cycle("sentinel")
        return task.id if task is not None else None

    result: Any = stack.run_db(_run)
    return result


def _cycle_state(stack: E2EStack) -> dict[str, Any]:
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> dict[str, Any]:
        open_cycle, last_opened_at = await get_board_program_engine(
            session
        ).cycle_state("sentinel")
        return {"open_cycle": open_cycle, "last_opened_at": last_opened_at}

    state: dict[str, Any] = stack.run_db(_run)
    return state


def _report_marker(stack: E2EStack, task_id: Any) -> dict[str, Any] | None:
    from roboco.db.tables import TaskTable
    from roboco.foundation.policy.content import markers
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any] | None:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        return markers.get_quality_report(row)

    result: dict[str, Any] | None = stack.run_db(_run)
    return result


def test_sentinel_loop_originates_dedups_and_completes(
    e2e_stack: E2EStack,
) -> None:
    stack = e2e_stack
    project_slug = _seed_system_and_auditor(stack)
    _arm(stack, project_slug)

    opened = _run_due_programs(stack)
    assert opened == ["sentinel"], opened

    state = _find_sentinel_task(stack)
    assert state["count"] == ONE, state
    row = state["rows"][0]
    assert row["status"] == "pending"
    assert row["assigned_to"] == _foundation.AGENTS["auditor"].uuid
    assert row["confirmed_by_human"] is False
    assert row["project_id"] is not None  # resolves against the RoboCo project

    # The dispatcher's own dev-work skip recognizes this exact task shape —
    # board_sentinel is board-dispatched (one-shot Auditor spawn), never
    # handed to the generic dev dispatch loop's give_me_work/claim path.
    from roboco.runtime.orchestrator import _is_non_dev_dispatch_source

    assert _is_non_dev_dispatch_source({"source": row["source"]}) is True

    # Second tick: the open cycle blocks re-origination — no second task.
    opened_again = _run_due_programs(stack)
    assert opened_again == [], opened_again
    state_after = _find_sentinel_task(stack)
    assert state_after["count"] == ONE, state_after

    cycle_before = _cycle_state(stack)
    assert cycle_before["open_cycle"] is True

    # The Auditor authors the report through the REAL do_server module +
    # /api/v1/do/propose_quality_report route + ContentActions + SENTINEL
    # service wiring — the same wiring-regression class check
    # test_periscope_loop.py guards (a verb granted in role_config but
    # missing from do_server._TOOLS is unreachable over MCP).
    auditor = ScriptedAgent(
        stack,
        _foundation.AGENTS["auditor"].uuid,
        "auditor",
        "auditor",
    )
    do_module = auditor._module("roboco.mcp.do_server")
    assert "propose_quality_report" in do_module._TOOLS, (
        "propose_quality_report missing from do_server._TOOLS — the MCP "
        "server has no way to expose it to any role"
    )
    assert "propose_quality_report" in do_module._REGISTERED_TOOLS, (
        "propose_quality_report is granted to auditor in role_config but "
        "absent from this agent's _register_tools() output — the manifest "
        "-> _register_tools -> callable chain dropped it"
    )

    headline = "Waived findings climbed sharply this week"
    env = expect_ok(
        auditor.do(
            "propose_quality_report",
            headline=headline,
            items=[
                {
                    "area": "waivers",
                    "observation": "Minor findings keep getting waived in one file",
                    "evidence": "5 waived-minor findings this week (prior: 1)",
                    "suggested_action": "Convert to a Pest Control bug task",
                }
            ],
            overall_assessment="Drift is concentrated, not systemic",
        ),
        "auditor propose_quality_report",
    )
    assert env.get("status") == "quality_report_proposed", env
    task_id_str = env.get("task_id")
    assert task_id_str == str(row["id"]), (
        "unlike x_feature's separate materialized draft, a quality report "
        f"completes the SAME exploration task: {env}"
    )

    task_id = UUID(task_id_str)
    from tests.e2e_smoke.arcs import task_state

    assert task_state(stack, task_id)["status"] == "completed"

    payload = _report_marker(stack, task_id)
    assert payload is not None
    assert payload["headline"] == headline
    assert len(payload["items"]) == ONE
    assert payload["items"][0]["area"] == "waivers"
    assert payload["overall_assessment"] == "Drift is concentrated, not systemic"

    # The exploration going terminal auto-closes the LEARN ledger row — no
    # manual close call is needed; propose_quality_report only sets the task
    # status, and BoardProgramEngine's own dedup check reconciles the rest.
    cycle_after = _cycle_state(stack)
    assert cycle_after["open_cycle"] is False, cycle_after

    # A fresh cycle can now open off-schedule (enabled + dedup only, no
    # cron-due check) — proves the auto-close actually unblocked dedup, not
    # just a stale read of a row that never really closed.
    reopened = _run_due_one(stack)
    assert reopened is not None
    assert reopened != row["id"]

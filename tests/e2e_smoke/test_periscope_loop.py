"""Scenario: the Periscope (Board Program) loop end to end.

Mirrors test_board_program_loop.py's roadmap arm -> originate -> dedup shape
(same RoboCo-project resolution — org scope means no per-project opt-in is
needed to RUN, but the exploration task's project_id still resolves against
the RoboCo project, a hard TaskService invariant every non-coordination task
carries) AND test_feature_spotlight.py's do_server wiring-regression check +
real-route propose call. The genuinely new piece: unlike either program, a
market brief completes the SAME exploration task it was authored on (no
separate materialized draft/item task) and its content must reach the
roadmap exploration prompt's cross-role injection helper.
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


def _seed_system_and_hom(stack: E2EStack) -> str:
    """Seed ``system`` + ``head-marketing`` at their FIXED foundation UUIDs,
    plus a project those two own — ``PeriscopeEngine._originate`` writes
    ``created_by``/``assigned_to`` straight from the static identity
    registry (not ``seed_company``'s random ``uuid4()`` agents; mirrors
    test_board_program_loop.py's identical ``_seed_system_and_po`` for
    roadmap), and the exploration task's ``project_id`` resolves against
    this project (the RoboCo-project FK anchor every org-scoped program's
    task still needs — see PeriscopeEngine's module docstring). Returns the
    project's slug.
    """
    from roboco.db.tables import AgentTable, ProjectTable
    from roboco.models import AgentRole, AgentStatus, Team

    slug = f"e2e-periscope-{uuid4().hex[:8]}"

    async def _run(session: AsyncSession) -> None:
        for agent_uuid, agent_slug, role, team in (
            (_foundation.AGENTS["system"].uuid, "system", AgentRole.SYSTEM, None),
            (
                _foundation.AGENTS["head-marketing"].uuid,
                "head-marketing",
                AgentRole.HEAD_MARKETING,
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
    flag exists for periscope) — and point ``self_heal_project_slug`` at the
    seeded project (the RoboCo-project resolution roadmap/x_feature share)."""
    from roboco.config import settings as cfg
    from roboco.db.tables import SystemSettingTable

    cfg.self_heal_project_slug = project_slug

    async def _run(session: AsyncSession) -> None:
        session.add(
            SystemSettingTable(key="board_program.periscope.enabled", value="true")
        )

    stack.run_db(_run)


def _run_due_programs(stack: E2EStack) -> list[str]:
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> list[str]:
        return await get_board_program_engine(session).run_due_programs()

    result: list[str] = stack.run_db(_run)
    return result


def _find_periscope_task(stack: E2EStack) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from roboco.services.task import PERISCOPE_SOURCE
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.source == PERISCOPE_SOURCE)
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
        task = await get_board_program_engine(session).open_program_cycle("periscope")
        return task.id if task is not None else None

    result: Any = stack.run_db(_run)
    return result


def _cycle_state(stack: E2EStack) -> dict[str, Any]:
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> dict[str, Any]:
        open_cycle, last_opened_at = await get_board_program_engine(
            session
        ).cycle_state("periscope")
        return {"open_cycle": open_cycle, "last_opened_at": last_opened_at}

    state: dict[str, Any] = stack.run_db(_run)
    return state


def _brief_marker(stack: E2EStack, task_id: Any) -> dict[str, Any] | None:
    from roboco.db.tables import TaskTable
    from roboco.foundation.policy.content import markers
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any] | None:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        return markers.get_market_brief(row)

    result: dict[str, Any] | None = stack.run_db(_run)
    return result


def _latest_brief_context(stack: E2EStack) -> str:
    from roboco.services.periscope_engine import get_periscope_engine

    async def _run(session: AsyncSession) -> str:
        return await get_periscope_engine(session).latest_brief_context()

    result: str = stack.run_db(_run)
    return result


def test_periscope_loop_originates_dedups_and_completes(
    e2e_stack: E2EStack,
) -> None:
    stack = e2e_stack
    project_slug = _seed_system_and_hom(stack)
    _arm(stack, project_slug)

    opened = _run_due_programs(stack)
    assert opened == ["periscope"], opened

    state = _find_periscope_task(stack)
    assert state["count"] == ONE, state
    row = state["rows"][0]
    assert row["status"] == "pending"
    assert row["assigned_to"] == _foundation.AGENTS["head-marketing"].uuid
    assert row["confirmed_by_human"] is False
    assert row["project_id"] is not None  # resolves against the RoboCo project

    # The dispatcher's own dev-work skip recognizes this exact task shape —
    # board_periscope is board-dispatched (one-shot HoM spawn), never handed
    # to the generic dev dispatch loop's give_me_work/claim path.
    from roboco.runtime.orchestrator import _is_non_dev_dispatch_source

    assert _is_non_dev_dispatch_source({"source": row["source"]}) is True

    # Second tick: the open cycle blocks re-origination — no second task.
    opened_again = _run_due_programs(stack)
    assert opened_again == [], opened_again
    state_after = _find_periscope_task(stack)
    assert state_after["count"] == ONE, state_after

    cycle_before = _cycle_state(stack)
    assert cycle_before["open_cycle"] is True

    # The Head of Marketing authors the brief through the REAL do_server
    # module + /api/v1/do/propose_market_brief route + ContentActions +
    # PERISCOPE service wiring — the same wiring-regression class check
    # test_feature_spotlight.py guards (a verb granted in role_config but
    # missing from do_server._TOOLS is unreachable over MCP).
    hom = ScriptedAgent(
        stack,
        _foundation.AGENTS["head-marketing"].uuid,
        "head-marketing",
        "head_marketing",
    )
    do_module = hom._module("roboco.mcp.do_server")
    assert "propose_market_brief" in do_module._TOOLS, (
        "propose_market_brief missing from do_server._TOOLS — the MCP "
        "server has no way to expose it to any role"
    )
    assert "propose_market_brief" in do_module._REGISTERED_TOOLS, (
        "propose_market_brief is granted to head_marketing in role_config "
        "but absent from this agent's _register_tools() output — the "
        "manifest -> _register_tools -> callable chain dropped it"
    )

    headline = "A rival tool shipped agentic PR review this week"
    env = expect_ok(
        hom.do(
            "propose_market_brief",
            headline=headline,
            findings=[
                {
                    "claim": "Competitor X launched an autonomous review agent",
                    "source_url": "https://example.com/competitor-x-launch",
                    "relevance": "Directly overlaps our own pr_reviewer role",
                }
            ],
            threats=["Feature parity gap on PR review"],
            opportunities=["Lean into our findings-ledger differentiator"],
            positioning_note="Emphasize the structured findings ledger",
        ),
        "hom propose_market_brief",
    )
    assert env.get("status") == "market_brief_proposed", env
    task_id_str = env.get("task_id")
    assert task_id_str == str(row["id"]), (
        "unlike x_feature's separate materialized draft, a market brief "
        f"completes the SAME exploration task: {env}"
    )

    task_id = UUID(task_id_str)
    from tests.e2e_smoke.arcs import task_state

    assert task_state(stack, task_id)["status"] == "completed"

    payload = _brief_marker(stack, task_id)
    assert payload is not None
    assert payload["headline"] == headline
    assert len(payload["findings"]) == ONE
    assert (
        payload["findings"][0]["source_url"]
        == "https://example.com/competitor-x-launch"
    )
    assert payload["threats"] == ["Feature parity gap on PR review"]

    # The exploration going terminal auto-closes the LEARN ledger row — no
    # manual close call is needed; propose_market_brief only sets the task
    # status, and BoardProgramEngine's own dedup check reconciles the rest.
    cycle_after = _cycle_state(stack)
    assert cycle_after["open_cycle"] is False, cycle_after

    # A fresh cycle can now open off-schedule (enabled + dedup only, no
    # cron-due check) — proves the auto-close actually unblocked dedup,
    # not just a stale read of a row that never really closed.
    reopened = _run_due_one(stack)
    assert reopened is not None
    assert reopened != row["id"]

    # The spec's cross-role feed: the filed brief reaches the roadmap
    # exploration prompt's injection helper.
    context = _latest_brief_context(stack)
    assert headline in context
    assert "Competitor X launched an autonomous review agent" in context
    assert "https://example.com/competitor-x-launch" in context

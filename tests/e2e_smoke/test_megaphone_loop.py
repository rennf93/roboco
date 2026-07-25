"""Scenario: the Megaphone (Board Program) loop end to end.

Mirrors test_periscope_loop.py's arm -> originate -> dedup shape (same
RoboCo-project resolution — org scope means no per-project opt-in is needed
to RUN, but the exploration task's project_id still resolves against the
RoboCo project, a hard TaskService invariant every non-coordination task
carries) PLUS the credentials gate (materialized drafts land in the X post
queue; drafting for nobody to post is pointless) AND
test_feature_spotlight.py's do_server wiring-regression check + real-route
propose call. The genuinely new piece: unlike periscope (which completes in
place with no separate artifact), a Megaphone post materializes into the
SAME X held-draft queue a feature spotlight does — zero new approval surface.
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
    """Seed ``system`` + ``head-marketing`` + ``secretary-1`` at their FIXED
    foundation UUIDs, plus a project those own — ``MegaphoneEngine._originate``
    writes ``created_by``/``assigned_to`` straight from the static identity
    registry (not ``seed_company``'s random ``uuid4()`` agents), and
    ``XEngine.materialize_editorial_post`` (via ``_originate_post``) does the
    same for the drafted post's owner (Secretary). Returns the project's slug.
    """
    from roboco.db.tables import AgentTable, ProjectTable
    from roboco.models import AgentRole, AgentStatus, Team

    slug = f"e2e-megaphone-{uuid4().hex[:8]}"

    async def _run(session: AsyncSession) -> None:
        for agent_uuid, agent_slug, role, team in (
            (_foundation.AGENTS["system"].uuid, "system", AgentRole.SYSTEM, None),
            (
                _foundation.AGENTS["head-marketing"].uuid,
                "head-marketing",
                AgentRole.HEAD_MARKETING,
                Team.BOARD,
            ),
            (
                _foundation.AGENTS["secretary-1"].uuid,
                "secretary-1",
                AgentRole.SECRETARY,
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


def _grant_credentials(stack: E2EStack) -> None:
    """Grant real X credentials — the program also refuses to originate
    without these even when armed (see MegaphoneEngine.run_cycle)."""
    from roboco.services.x_credentials import get_x_credentials_service

    async def _run(session: AsyncSession) -> None:
        await get_x_credentials_service(session).set_credentials(
            api_key="k", api_secret="s", access_token="t", access_token_secret="ts"
        )

    stack.run_db(_run)


def _run_due_programs(stack: E2EStack) -> list[str]:
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> list[str]:
        return await get_board_program_engine(session).run_due_programs()

    result: list[str] = stack.run_db(_run)
    return result


def _find_megaphone_task(stack: E2EStack) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from roboco.services.task import MEGAPHONE_SOURCE
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.source == MEGAPHONE_SOURCE)
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


def _cycle_state(stack: E2EStack) -> dict[str, Any]:
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> dict[str, Any]:
        open_cycle, last_opened_at = await get_board_program_engine(
            session
        ).cycle_state("megaphone")
        return {"open_cycle": open_cycle, "last_opened_at": last_opened_at}

    state: dict[str, Any] = stack.run_db(_run)
    return state


def _x_draft_state(stack: E2EStack, task_id: UUID) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        return {
            "status": str(row.status),
            "source": row.source,
            "confirmed_by_human": row.confirmed_by_human,
        }

    state: dict[str, Any] = stack.run_db(_run)
    return state


def test_megaphone_loop_originates_dedups_proposes_and_holds_draft(
    e2e_stack: E2EStack,
) -> None:
    stack = e2e_stack
    project_slug = _seed_system_and_hom(stack)

    # No credentials yet, but armed: origination refuses (spec — drafting for
    # nobody to post is pointless).
    from roboco.config import settings as cfg
    from roboco.db.tables import SystemSettingTable

    cfg.self_heal_project_slug = project_slug

    async def _arm(session: AsyncSession) -> None:
        session.add(
            SystemSettingTable(key="board_program.megaphone.enabled", value="true")
        )

    stack.run_db(_arm)
    opened_no_creds = _run_due_programs(stack)
    assert "megaphone" not in opened_no_creds

    # Grant credentials — now a cycle opens.
    _grant_credentials(stack)
    opened = _run_due_programs(stack)
    assert opened == ["megaphone"], opened

    state = _find_megaphone_task(stack)
    assert state["count"] == ONE, state
    row = state["rows"][0]
    assert row["status"] == "pending"
    assert row["assigned_to"] == _foundation.AGENTS["head-marketing"].uuid
    assert row["confirmed_by_human"] is False
    assert row["project_id"] is not None  # resolves against the RoboCo project

    # The dispatcher's own dev-work skip recognizes this exact task shape —
    # board_megaphone is board-dispatched (one-shot HoM spawn), never handed
    # to the generic dev dispatch loop's give_me_work/claim path.
    from roboco.runtime.orchestrator import _is_non_dev_dispatch_source

    assert _is_non_dev_dispatch_source({"source": row["source"]}) is True

    # Second tick: the open cycle blocks re-origination — no second task.
    opened_again = _run_due_programs(stack)
    assert opened_again == [], opened_again
    state_after = _find_megaphone_task(stack)
    assert state_after["count"] == ONE, state_after

    cycle_before = _cycle_state(stack)
    assert cycle_before["open_cycle"] is True

    # The Head of Marketing authors the post through the REAL do_server
    # module + /api/v1/do/propose_editorial_post route + ContentActions +
    # XEngine wiring — the same wiring-regression class check
    # test_feature_spotlight.py guards (a verb granted in role_config but
    # missing from do_server._TOOLS is unreachable over MCP).
    hom = ScriptedAgent(
        stack,
        _foundation.AGENTS["head-marketing"].uuid,
        "head-marketing",
        "head_marketing",
    )
    do_module = hom._module("roboco.mcp.do_server")
    assert "propose_editorial_post" in do_module._TOOLS, (
        "propose_editorial_post missing from do_server._TOOLS — the MCP "
        "server has no way to expose it to any role"
    )
    assert "propose_editorial_post" in do_module._REGISTERED_TOOLS, (
        "propose_editorial_post is granted to head_marketing in role_config "
        "but absent from this agent's _register_tools() output — the "
        "manifest -> _register_tools -> callable chain dropped it"
    )

    body = (
        "This week the fleet shipped MegaTask waves, a PR-review gate, and "
        "the findings ledger — real delivery, not a highlight reel."
    )
    env = expect_ok(
        hom.do(
            "propose_editorial_post",
            angle="dev_log",
            body=body,
            rationale="Dev-log cadence keeps the audience close to real shipping.",
        ),
        "hom propose_editorial_post",
    )
    assert env.get("status") == "editorial_post_proposed", env
    task_id_str = env.get("task_id")
    assert task_id_str and task_id_str != str(row["id"]), (
        f"expected a NEW draft task id distinct from the exploration task: {env}"
    )

    draft_id = UUID(task_id_str)
    draft = _x_draft_state(stack, draft_id)
    assert draft["source"] == "x_editorial", draft
    assert draft["confirmed_by_human"] is False, draft
    assert draft["status"] == "pending", draft  # held, awaiting the CEO's review

    from tests.e2e_smoke.arcs import task_state

    assert task_state(stack, UUID(str(row["id"])))["status"] == "completed"

    # The exploration going terminal auto-closes the LEARN ledger row.
    cycle_after = _cycle_state(stack)
    assert cycle_after["open_cycle"] is False, cycle_after

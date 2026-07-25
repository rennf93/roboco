"""Scenario: the War Room (Board Program) loop end to end.

Mirrors test_periscope_loop.py's arm -> originate -> dedup shape (org scope,
no per-project opt-in to RUN, RoboCo-project FK anchor) AND
test_feature_spotlight.py's do_server wiring-regression check + real-route
propose call. The genuinely new piece War Room adds: EVENT trigger with a
REAL originator (unlike Coroner's always-None stub — proven here via
``open_program_cycle`` standing in for the CEO's "run now" call, since this
harness has no HTTP client), an X-credentials gate the origination step must
clear, and a per-item materialization loop (N ordered ``x_campaign`` drafts
from one ``propose_campaign`` call, not one item on the exploration task
itself).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from roboco.foundation import identity as _foundation
from tests.e2e_smoke.harness import ScriptedAgent, expect_ok

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack

ONE = 1
THREE = 3


def _future(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def _seed_system_hom_and_secretary(stack: E2EStack) -> tuple[Any, str]:
    """Seed ``system`` + ``head-marketing`` + ``secretary-1`` at their FIXED
    foundation UUIDs, plus a project those own — ``WarRoomEngine._originate``
    assigns the campaign-planning task to ``head-marketing``'s fixed UUID
    (not ``seed_company``'s random ``uuid4()`` row, which would also collide
    on the unique agent slug), and ``XEngine._originate_post`` assigns every
    materialized draft to ``secretary-1``'s fixed UUID — both FKs need real
    rows there. Returns ``(project_id, project_slug)`` — the id scopes every
    read below to THIS test's own rows: ``ROBOCO_TEST_DB_*`` is the same
    physical Postgres a non-e2e suite run earlier in the same invocation
    also wrote to (e.g. test_board_programs_api.py's own war_room run-now
    test leaves a CANCELLED ``board_war_room`` row behind, cleaned up but
    never deleted), so an unscoped "count every board_war_room row" query
    would double-count across runs.
    """
    from roboco.db.tables import AgentTable, ProjectTable
    from roboco.models import AgentRole, AgentStatus, Team

    slug = f"e2e-war-room-{uuid4().hex[:8]}"
    project_id = uuid4()

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
    return project_id, slug


def _arm(stack: E2EStack, project_slug: str) -> None:
    """Arm via the settings-store key — the ONLY arming path (no legacy env
    flag exists for war_room) — point ``self_heal_project_slug`` at the
    seeded project, and seed X credentials: ``WarRoomEngine``'s own creds
    gate would otherwise no-op every origination call, mirroring XEngine's
    release/spotlight guard."""
    from roboco.config import settings as cfg
    from roboco.db.tables import SystemSettingTable
    from roboco.services.x_credentials import get_x_credentials_service

    cfg.self_heal_project_slug = project_slug

    async def _run(session: AsyncSession) -> None:
        session.add(
            SystemSettingTable(key="board_program.war_room.enabled", value="true")
        )
        await get_x_credentials_service(session).set_credentials(
            api_key="ak-test",
            api_secret="as-test",
            access_token="at-test",
            access_token_secret="ats-test",
        )

    stack.run_db(_run)


def _run_now(stack: E2EStack) -> Any:
    """The CEO's "run now" seam — ``open_program_cycle`` does not check
    trigger kind, only ``run_due_programs`` (the cron loop) does, so this is
    the real EVENT-program run-now path."""
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> Any:
        task = await get_board_program_engine(session).open_program_cycle("war_room")
        return task.id if task is not None else None

    result: Any = stack.run_db(_run)
    return result


def _run_due_programs(stack: E2EStack) -> list[str]:
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> list[str]:
        return await get_board_program_engine(session).run_due_programs()

    result: list[str] = stack.run_db(_run)
    return result


def _find_war_room_task(stack: E2EStack, project_id: Any) -> dict[str, Any]:
    """Scoped to ``project_id`` — see ``_seed_system_hom_and_secretary``'s
    docstring for why an unscoped query double-counts against the shared
    test Postgres."""
    from roboco.db.tables import TaskTable
    from roboco.services.task import WAR_ROOM_SOURCE
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(
                        TaskTable.source == WAR_ROOM_SOURCE,
                        TaskTable.project_id == project_id,
                    )
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


def _campaign_drafts(stack: E2EStack, project_id: Any) -> list[dict[str, Any]]:
    """Scoped to ``project_id`` for the same reason as ``_find_war_room_task``."""
    from roboco.db.tables import TaskTable
    from roboco.foundation.policy.content import markers
    from roboco.services.task import X_CAMPAIGN_SOURCE
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(
                        TaskTable.source == X_CAMPAIGN_SOURCE,
                        TaskTable.project_id == project_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": r.id,
                "status": str(r.status),
                "confirmed_by_human": r.confirmed_by_human,
                "ref": markers.get_x_campaign_ref(r),
            }
            for r in rows
        ]

    result: list[dict[str, Any]] = stack.run_db(_run)
    return result


def test_war_room_loop_run_now_then_propose_campaign(e2e_stack: E2EStack) -> None:
    stack = e2e_stack
    project_id, project_slug = _seed_system_hom_and_secretary(stack)
    _arm(stack, project_slug)

    # EVENT programs are never cron-due — proves the loop itself never opens
    # war_room, unlike the run-now seam below.
    opened_by_loop = _run_due_programs(stack)
    assert "war_room" not in opened_by_loop, opened_by_loop

    # THE run-now-works-for-EVENT proof: war_room's originator is REAL
    # (unlike Coroner's always-None stub), so this must actually originate.
    exploration_id = _run_now(stack)
    assert exploration_id is not None

    state = _find_war_room_task(stack, project_id)
    assert state["count"] == ONE, state
    row = state["rows"][0]
    assert row["id"] == exploration_id
    assert row["status"] == "pending"
    assert row["assigned_to"] == _foundation.AGENTS["head-marketing"].uuid
    assert row["confirmed_by_human"] is False
    assert row["project_id"] is not None  # resolves against the RoboCo project

    # The dispatcher's own dev-work skip recognizes this exact task shape.
    from roboco.runtime.orchestrator import _is_non_dev_dispatch_source

    assert _is_non_dev_dispatch_source({"source": row["source"]}) is True

    # A second run-now while one campaign is open is refused — dedup.
    second = _run_now(stack)
    assert second is None

    # The Head of Marketing authors the campaign through the REAL do_server
    # module + /api/v1/do/propose_campaign route + ContentActions + XEngine
    # wiring — the wiring-regression class check test_feature_spotlight.py
    # guards (a verb granted in role_config but missing from do_server._TOOLS
    # is unreachable over MCP).
    hom = ScriptedAgent(
        stack,
        _foundation.AGENTS["head-marketing"].uuid,
        "head-marketing",
        "head_marketing",
    )
    do_module = hom._module("roboco.mcp.do_server")
    assert "propose_campaign" in do_module._TOOLS, (
        "propose_campaign missing from do_server._TOOLS — the MCP server "
        "has no way to expose it to any role"
    )
    assert "propose_campaign" in do_module._REGISTERED_TOOLS, (
        "propose_campaign is granted to head_marketing in role_config but "
        "absent from this agent's _register_tools() output — the manifest "
        "-> _register_tools -> callable chain dropped it"
    )

    posts = [
        {
            "body": "Something big is coming to RoboCo. Watch this space.",
            "publish_after": _future(1),
            "stage_label": "teaser",
        },
        {
            "body": "War Room is live: plan a whole X campaign in one cycle.",
            "publish_after": _future(25),
            "stage_label": "launch",
        },
        {
            "body": "War Room posts carry a recommended publish time — you "
            "still approve each one yourself.",
            "publish_after": _future(49),
            "stage_label": "follow_up",
        },
    ]
    env = expect_ok(
        hom.do(
            "propose_campaign",
            campaign_name="War Room launch",
            posts=posts,
        ),
        "hom propose_campaign",
    )
    assert env.get("status") == "campaign_proposed", env
    assert env.get("task_id") == str(exploration_id), (
        "propose_campaign completes the SAME exploration task it authored "
        f"against, no separate materialized cycle task: {env}"
    )

    from tests.e2e_smoke.arcs import task_state

    assert task_state(stack, UUID(str(exploration_id)))["status"] == "completed"

    drafts = _campaign_drafts(stack, project_id)
    assert len(drafts) == THREE, drafts
    ordered = sorted(drafts, key=lambda d: d["ref"]["sequence"])
    assert [d["ref"]["stage_label"] for d in ordered] == [
        "teaser",
        "launch",
        "follow_up",
    ]
    assert [d["ref"]["sequence"] for d in ordered] == [1, 2, THREE]
    assert all(d["ref"]["campaign_name"] == "War Room launch" for d in ordered)
    assert all(d["status"] == "pending" for d in ordered)  # held, awaiting the CEO
    assert all(d["confirmed_by_human"] is False for d in ordered)

    # publish_after strictly ascends across the campaign, matching the
    # order the Head of Marketing proposed it in.
    timestamps = [datetime.fromisoformat(d["ref"]["publish_after"]) for d in ordered]
    assert timestamps == sorted(timestamps)

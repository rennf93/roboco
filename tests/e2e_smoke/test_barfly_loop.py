"""Scenario: the Barfly (Board Program) loop end to end.

Mirrors test_periscope_loop.py's arm -> originate -> dedup shape AND
test_feature_spotlight.py's do_server wiring-regression check + real-route
propose call. The genuinely new pieces: (1) origination needs a configured X
client (search_recent), unlike periscope which needs none — faked here via
``roboco.services.barfly_engine.build_x_client`` (uvicorn runs in-thread, same
process, so the patch reaches the live route handler too); (2) unlike a
market brief (one report, completes its own task) a Barfly proposal
materializes N SEPARATE held x_barfly drafts through the shared
_originate_post chokepoint while completing the exploration task itself —
the x_feature-style asymmetry, multiplied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

from roboco.foundation import identity as _foundation
from tests.e2e_smoke.harness import ScriptedAgent, expect_ok

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack

ONE = 1
TWO = 2
ZERO = 0

_CANDIDATES: list[dict[str, Any]] = [
    {"id": "e2e-1", "text": "we should try a multi-agent coding org", "likes": 3},
    {"id": "e2e-2", "text": "autonomous software teams are the future", "likes": 1},
]
# A second, disjoint id set for the reopen check below — the first batch's
# ids are already in the seen ledger by then, so reusing them would starve
# the reopened cycle of any surviving candidate.
_CANDIDATES_ROUND_2: list[dict[str, Any]] = [
    {
        "id": "e2e-3",
        "text": "curious how agent teams handle merge conflicts",
        "likes": 2,
    },
]


class _FakeSearchClient:
    """Configured stub returned by the patched ``build_x_client`` — never
    touches the network."""

    def __init__(self, candidates: list[dict[str, Any]] | None = None) -> None:
        self._candidates = candidates if candidates is not None else _CANDIDATES

    @property
    def configured(self) -> bool:
        return True

    async def search_recent(self, query: str, max_results: int) -> list[Any]:
        from roboco.services.x_client import XMention

        _ = (query, max_results)
        return [
            XMention(
                id=c["id"],
                author_id=f"author-{c['id']}",
                text=c["text"],
                like_count=c["likes"],
                reply_count=0,
                retweet_count=0,
            )
            for c in self._candidates
        ]

    async def post_tweet(self, text: str, **_kwargs: Any) -> Any:
        raise AssertionError(f"this e2e scenario never approves/posts: {text!r}")

    async def fetch_mentions(self, since_id: str | None, max_results: int) -> list[Any]:
        _ = (since_id, max_results)
        return []


def _seed_system_and_hom(stack: E2EStack) -> str:
    """Seed ``system`` + ``head-marketing`` + ``secretary-1`` at their FIXED
    foundation UUIDs (BarflyEngine/_originate_post write created_by/
    assigned_to straight from the static identity registry), plus a project
    those own — the exploration task's FK anchor. Returns the project's slug.
    """
    from roboco.db.tables import AgentTable, ProjectTable
    from roboco.models import AgentRole, AgentStatus, Team

    slug = f"e2e-barfly-{uuid4().hex[:8]}"

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


def _arm(stack: E2EStack, project_slug: str) -> None:
    """Arm via the settings-store key (the ONLY arming path — no legacy env
    flag exists for barfly) + stored X credentials (BarflyEngine's creds
    gate) + point ``self_heal_project_slug`` at the seeded project."""
    from roboco.config import settings as cfg
    from roboco.db.tables import SystemSettingTable
    from roboco.services.x_credentials import get_x_credentials_service

    cfg.self_heal_project_slug = project_slug

    async def _run(session: AsyncSession) -> None:
        session.add(
            SystemSettingTable(key="board_program.barfly.enabled", value="true")
        )
        await get_x_credentials_service(session).set_credentials(
            api_key="k",
            api_secret="s",
            access_token="t",
            access_token_secret="ts",
        )

    stack.run_db(_run)


def _run_due_programs(stack: E2EStack) -> list[str]:
    from roboco.services.board_programs import get_board_program_engine

    async def _run(session: AsyncSession) -> list[str]:
        return await get_board_program_engine(session).run_due_programs()

    result: list[str] = stack.run_db(_run)
    return result


def _find_barfly_task(stack: E2EStack) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from roboco.foundation.policy.content import markers
    from roboco.services.task import BARFLY_SOURCE
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.source == BARFLY_SOURCE)
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
                    "candidates": markers.get_barfly_candidates(r),
                }
                for r in rows
            ],
        }

    state: dict[str, Any] = stack.run_db(_run)
    return state


def _x_barfly_drafts(stack: E2EStack) -> list[dict[str, Any]]:
    from roboco.db.tables import TaskTable
    from roboco.foundation.policy.content import markers
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.source == "x_barfly")
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
                "body": markers.get_x_draft_body(r),
                "reply_ref": markers.get_barfly_reply_ref(r),
            }
            for r in rows
        ]

    result: list[dict[str, Any]] = stack.run_db(_run)
    return result


def test_barfly_loop_originates_dedups_proposes_and_materializes(
    e2e_stack: E2EStack,
) -> None:
    stack = e2e_stack
    project_slug = _seed_system_and_hom(stack)
    _arm(stack, project_slug)

    with patch(
        "roboco.services.barfly_engine.build_x_client",
        return_value=_FakeSearchClient(),
    ):
        opened = _run_due_programs(stack)
    assert opened == ["barfly"], opened

    state = _find_barfly_task(stack)
    assert state["count"] == ONE, state
    row = state["rows"][0]
    assert row["status"] == "pending"
    assert row["assigned_to"] == _foundation.AGENTS["head-marketing"].uuid
    assert row["confirmed_by_human"] is False
    assert row["project_id"] is not None
    candidate_ids = {c["id"] for c in row["candidates"]}
    assert candidate_ids == {"e2e-1", "e2e-2"}

    # board_barfly is board-dispatched (one-shot HoM spawn), never handed to
    # the generic dev dispatch loop's give_me_work/claim path.
    from roboco.runtime.orchestrator import _is_non_dev_dispatch_source

    assert _is_non_dev_dispatch_source({"source": row["source"]}) is True

    # Second tick — a client whose configured check passes but whose
    # search_recent errors if ever called: the open-cycle dedup must block
    # BEFORE a real search runs, even though the creds/configured check
    # itself still runs ahead of dedup (mirrors XEngine's own ordering).
    class _BoomIfSearched(_FakeSearchClient):
        async def search_recent(self, query: str, max_results: int) -> list[Any]:
            raise AssertionError(
                f"must not search (query={query!r}, max_results={max_results}) "
                "while dedup-blocked"
            )

    with patch(
        "roboco.services.barfly_engine.build_x_client",
        return_value=_BoomIfSearched(),
    ):
        opened_again = _run_due_programs(stack)
    assert opened_again == [], opened_again
    state_after = _find_barfly_task(stack)
    assert state_after["count"] == ONE, state_after

    # The Head of Marketing authors replies through the REAL do_server module
    # + /api/v1/do/propose_conversation_replies route + ContentActions +
    # XEngine wiring — the wiring-regression class check test_feature_
    # spotlight.py guards (a verb granted in role_config but missing from
    # do_server._TOOLS is unreachable over MCP).
    hom = ScriptedAgent(
        stack,
        _foundation.AGENTS["head-marketing"].uuid,
        "head-marketing",
        "head_marketing",
    )
    do_module = hom._module("roboco.mcp.do_server")
    assert "propose_conversation_replies" in do_module._TOOLS, (
        "propose_conversation_replies missing from do_server._TOOLS — the "
        "MCP server has no way to expose it to any role"
    )
    assert "propose_conversation_replies" in do_module._REGISTERED_TOOLS, (
        "propose_conversation_replies is granted to head_marketing in "
        "role_config but absent from this agent's _register_tools() output "
        "— the manifest -> _register_tools -> callable chain dropped it"
    )

    env = expect_ok(
        hom.do(
            "propose_conversation_replies",
            items=[
                {
                    "tweet_id": "e2e-1",
                    "reply_body": "That's exactly what request_sandbox() gives you.",
                    "rationale": "Directly answers what they're describing.",
                },
                {
                    "tweet_id": "e2e-2",
                    "reply_body": "We built exactly that — 25 agents, one CEO.",
                    "rationale": "Speaks directly to the premise of their post.",
                },
            ],
        ),
        "hom propose_conversation_replies",
    )
    assert env.get("status") == "conversation_replies_proposed", env
    assert env.get("task_id") == str(row["id"])
    materialized_ids = env.get("context_briefing", {}).get("materialized_task_ids", [])
    assert len(materialized_ids) == TWO, env

    from tests.e2e_smoke.arcs import task_state

    exploration_id = UUID(env["task_id"])
    assert task_state(stack, exploration_id)["status"] == "completed"

    drafts = _x_barfly_drafts(stack)
    assert len(drafts) == TWO, drafts
    tweet_ids = {d["reply_ref"]["tweet_id"] for d in drafts}
    assert tweet_ids == {"e2e-1", "e2e-2"}
    for draft in drafts:
        assert draft["status"] == "pending"
        assert draft["confirmed_by_human"] is False  # HELD; the CEO decides
        assert draft["body"]

    # The exploration going terminal auto-closes the LEARN ledger row — a
    # fresh cycle can open off-schedule (enabled + dedup only) proving it.
    from roboco.services.board_programs import get_board_program_engine

    async def _reopen(session: AsyncSession) -> Any:
        return await get_board_program_engine(session).open_program_cycle("barfly")

    with patch(
        "roboco.services.barfly_engine.build_x_client",
        return_value=_FakeSearchClient(_CANDIDATES_ROUND_2),
    ):
        reopened_id = stack.run_db(_reopen)
    assert reopened_id is not None
    assert reopened_id != row["id"]

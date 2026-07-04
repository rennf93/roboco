"""Scenario: the Head of Marketing's feature-spotlight draft, end to end.

Regression coverage for the class of bug where a new content verb is wired at
the role-config + content-actions + route layers but never added to
do_server's ``_TOOLS`` registry — ``_register_tools()`` then registers only
the intersection of granted verbs and ``_TOOLS``, silently dropping the verb
so the agent can never call it. This is exactly what happened to
``propose_feature_spotlight`` in the v0.18.0 feature-spotlight work (see
``tests/unit/mcp_servers/test_do_server_tool_coverage.py`` for the equivalent
unit-level registry audit across every role).

Drives the REAL do_server module — reloaded with a Head-of-Marketing
manifest built from the REAL role_config, exactly as ScriptedAgent does for
every other role — through the REAL /api/v1/do/propose_feature_spotlight
route, REAL ContentActions, and REAL XEngine, then asserts the held X-queue
draft (source=x_feature, confirmed_by_human=False) exists and the
exploration task completed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from tests.e2e_smoke.arcs import seed_company, seed_project, seed_task, task_state
from tests.e2e_smoke.harness import ScriptedAgent, expect_ok

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.arcs import Company
    from tests.e2e_smoke.harness import E2EStack


def _seed_system_and_secretary(stack: E2EStack) -> None:
    """Seed ``system`` + ``secretary-1`` at their FIXED foundation UUIDs.

    ``XEngine.materialize_feature_spotlight`` writes the held draft's
    ``created_by`` / ``assigned_to`` straight from the static identity
    registry (not a DB lookup keyed by role), so those exact ids must exist
    as real agent rows for the FK to resolve — ``seed_company``'s random
    ``uuid4()`` agents don't cover this. Idempotent (mirrors
    ``tests/unit/services/test_x_engine.py``'s ``_seed``): safe if ever
    called more than once against the same stack.
    """
    from roboco.db.tables import AgentTable
    from roboco.foundation import identity as _foundation
    from roboco.models import AgentRole, AgentStatus

    async def _run(session: AsyncSession) -> None:
        for agent_uuid, slug, role in (
            (_foundation.AGENTS["system"].uuid, "system", AgentRole.SYSTEM),
            (
                _foundation.AGENTS["secretary-1"].uuid,
                "secretary-1",
                AgentRole.SECRETARY,
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
                    team=None,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt=slug,
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )

    stack.run_db(_run)


def _seed_feature_exploration(
    stack: E2EStack, company: Company, project_id: Any
) -> Any:
    """A held ``x_feature_exploration`` task assigned to the Head of Marketing.

    Mirrors ``XEngine._originate_feature_exploration``'s shape (team/
    task_type/nature/complexity/source/confirmed_by_human), seeded directly
    the same way ``seed_hierarchy`` stands in for the orchestrator's own
    task creation.
    """
    from roboco.models import Team
    from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
    from roboco.services.task import X_FEATURE_EXPLORATION_SOURCE

    return seed_task(
        stack,
        title="X feature-spotlight exploration",
        description=(
            "Investigate shipped capabilities (CHANGELOG, feature flags, "
            "docs/map, charter, KB) and propose ONE feature-spotlight post "
            "for an under-publicized, not-yet-covered capability."
        ),
        acceptance_criteria=[
            "propose_feature_spotlight() is called once with an "
            "under-publicized, not-yet-covered feature"
        ],
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        team=Team.BOARD,
        project_id=project_id,
        created_by=company.main_pm_id,
        assigned_to=company.hom_id,
        status=TaskStatus.PENDING,
        source=X_FEATURE_EXPLORATION_SOURCE,
        confirmed_by_human=False,
    )


def _x_draft_state(stack: E2EStack, task_id: UUID) -> dict[str, Any]:
    """``source`` / ``confirmed_by_human`` for the materialized X-queue draft
    — the held-artifact fields ``task_state()`` doesn't carry."""
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


def test_feature_spotlight_proposal_creates_held_draft(e2e_stack: E2EStack) -> None:
    stack = e2e_stack
    company = seed_company(stack)
    _seed_system_and_secretary(stack)
    project_id, _project_slug = seed_project(stack, company)
    exploration_id = _seed_feature_exploration(stack, company, project_id)

    hom = ScriptedAgent(stack, company.hom_id, "head-marketing", "head_marketing")

    # The bug's exact surface: propose_feature_spotlight shipped granted
    # (role_config) + implemented (ContentActions) + routed (api/v1/do), but
    # do_server carried neither the wrapper function nor the _TOOLS entry, so
    # no spawned Head of Marketing could ever reach it over MCP. Asserting
    # the registry directly — not just relying on the call below succeeding
    # — means a future regression that drops ONLY the _TOOLS entry (keeping
    # the wrapper function) still fails here, even though that narrower
    # variant would not raise AttributeError from the getattr() in .do().
    do_module = hom._module("roboco.mcp.do_server")
    assert "propose_feature_spotlight" in do_module._TOOLS, (
        "propose_feature_spotlight missing from do_server._TOOLS — the MCP "
        "server has no way to expose it to any role"
    )
    assert "propose_feature_spotlight" in do_module._REGISTERED_TOOLS, (
        "propose_feature_spotlight is granted to head_marketing in "
        "role_config but absent from this agent's _register_tools() output "
        "— the manifest -> _register_tools -> callable chain dropped it"
    )

    feature_slug = f"e2e-feature-{uuid4().hex[:8]}"
    env = expect_ok(
        hom.do(
            "propose_feature_spotlight",
            feature_slug=feature_slug,
            feature_title="Organizational Memory Loop",
            body=(
                "Did you know RoboCo agents distill one lesson per completed "
                "task and reuse it on the next matching claim? Institutional "
                "memory, built in."
            ),
        ),
        "hom propose_feature_spotlight",
    )
    assert env.get("status") == "feature_spotlight_proposed", env
    task_id_str = env.get("task_id")
    assert task_id_str and task_id_str != str(exploration_id), (
        f"expected a NEW draft task id distinct from the exploration task: {env}"
    )

    draft = _x_draft_state(stack, UUID(task_id_str))
    assert draft["source"] == "x_feature", draft
    assert draft["confirmed_by_human"] is False, draft
    assert draft["status"] == "pending", draft  # held, awaiting the CEO's review

    assert task_state(stack, exploration_id)["status"] == "completed"

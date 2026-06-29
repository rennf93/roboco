"""TaskService coverage — status transitions + claim/unclaim/cancel paths.

Covers the core lifecycle methods (start, claim, unclaim variants, pause,
resume, cancel cascades, block/unblock, fail_qa with original-developer
reassignment, ceo_approve/ceo_reject, escalation chains).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentTable,
    JournalEntryTable,
    ProductTable,
    ProjectTable,
    WorkSessionTable,
)
from roboco.events import EventType
from roboco.foundation.policy.content import markers
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    BlockerResolverType,
    Complexity,
    JournalEntryType,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.task import TaskCreateRequest
from roboco.models.work_session import WorkSessionStatus
from roboco.seeds.initial_data import AGENT_UUIDS
from roboco.services.base import NotFoundError
from roboco.services.task import SoftBlockInfo, TaskService
from sqlalchemy import Table, select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def task_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="T-Proj",
        slug=f"t-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": TaskService(db_session),
        "agent_id": agent.id,
        "project_id": project.id,
        "db": db_session,
    }


def _req(setup: dict, **overrides: Any) -> TaskCreateRequest:
    return TaskCreateRequest(
        title=overrides.pop("title", "t"),
        description=overrides.pop("description", "d"),
        acceptance_criteria=overrides.pop("acceptance_criteria", ["ac"]),
        team=overrides.pop("team", Team.BACKEND),
        created_by=setup["agent_id"],
        project_id=setup["project_id"],
        task_type=overrides.pop("task_type", TaskType.CODE),
        nature=overrides.pop("nature", TaskNature.TECHNICAL),
        estimated_complexity=overrides.pop("estimated_complexity", Complexity.MEDIUM),
        **overrides,
    )


# ---------------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.start(uuid4()) is None


@pytest.mark.asyncio
async def test_start_returns_none_when_invalid_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    assert await svc.start(task.id, agent_role="developer") is None


@pytest.mark.asyncio
async def test_start_returns_none_when_no_plan(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.plan = None
    await db_session.flush()
    assert await svc.start(task.id, agent_role="developer") is None


@pytest.mark.asyncio
async def test_start_with_plan_advances_to_in_progress(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    task.branch_name = "feature/backend/AAAAAAA"
    task.plan = {"text": "step 1"}
    await db_session.flush()
    started = await svc.start(
        task.id, agent_id=task_setup["agent_id"], agent_role="developer"
    )
    assert started is not None
    assert started.status == TaskStatus.IN_PROGRESS
    assert started.started_at is not None


@pytest.mark.asyncio
async def test_start_batch_umbrella_advances_without_branch(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """A MegaTask umbrella (batch_id, no project/product, no branch) is branchless
    coordination — the claimed->in_progress branch gate must be skipped for it so
    it can reach in_progress and delegate, exactly like a product fan-out root."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    task.project_id = None
    task.product_id = None
    task.batch_id = uuid4()
    task.parent_task_id = None
    task.branch_name = None
    task.plan = {"text": "sequence the waves"}
    await db_session.flush()
    started = await svc.start(
        task.id, agent_id=task_setup["agent_id"], agent_role="developer"
    )
    assert started is not None
    assert started.status == TaskStatus.IN_PROGRESS
    assert started.branch_name is None


@pytest.mark.asyncio
async def test_start_returns_none_when_ownership_fails(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Non-assignee agent cannot start the task."""
    svc = task_setup["svc"]
    other = AgentTable(
        id=uuid4(),
        name="Other",
        slug=f"other-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = other.id
    task.plan = {"text": "p"}
    await db_session.flush()
    out = await svc.start(
        task.id, agent_id=task_setup["agent_id"], agent_role="developer"
    )
    assert out is None


@pytest.mark.asyncio
async def test_start_paused_task_resumes_in_progress(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    task.assigned_to = task_setup["agent_id"]
    task.plan = {"text": "p"}
    await db_session.flush()
    started = await svc.start(task.id, agent_role="developer")
    assert started is not None
    assert started.status == TaskStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# unclaim_for_reaper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unclaim_for_reaper_resets_claim_but_keeps_owner(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    task.active_claimant_id = task_setup["agent_id"]
    await db_session.flush()
    await svc.unclaim_for_reaper(task.id)
    refreshed = await svc.get(task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PENDING
    # Ownership is preserved so the same agent resumes the task once it
    # re-dispatches — the task must never land in an ownerless pending limbo.
    assert refreshed.assigned_to == task_setup["agent_id"]
    assert refreshed.claimed_by == task_setup["agent_id"]
    # The live claim is released so the reaper/dispatcher can re-spawn cleanly.
    assert refreshed.active_claimant_id is None


@pytest.mark.asyncio
async def test_unclaim_for_reaper_skips_when_status_already_pending(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    # Should not raise — branch returns immediately.
    await svc.unclaim_for_reaper(task.id)


@pytest.mark.asyncio
async def test_release_dependency_blocked_claim_keeps_owner(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Dependency-release returns to pending without orphaning the owner.

    Shares ``_force_unclaim_to_pending`` with the reaper, so it must give the
    same guarantee: the same agent resumes once the upstream dependency lands.
    The work-in-progress branch is forgotten so the re-claim cuts fresh off the
    (now-updated) integration tip.
    """
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    task.claimed_by = task_setup["agent_id"]
    task.active_claimant_id = task_setup["agent_id"]
    task.branch_name = "feature/backend/ABC12345"
    await db_session.flush()
    await svc.release_dependency_blocked_claim(task.id)
    refreshed = await svc.get(task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PENDING
    assert refreshed.assigned_to == task_setup["agent_id"]
    assert refreshed.claimed_by == task_setup["agent_id"]
    assert refreshed.active_claimant_id is None
    assert refreshed.branch_name is None


# ---------------------------------------------------------------------------
# unclaim_for_agent — all error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unclaim_for_agent_returns_none_when_wrong_assignee(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    other = AgentTable(
        id=uuid4(),
        name="Other",
        slug=f"other-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = other.id
    await db_session.flush()
    assert await svc.unclaim_for_agent(task.id, agent_id=task_setup["agent_id"]) is None


@pytest.mark.asyncio
async def test_unclaim_for_agent_releases_pending_assignment(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """#176: an agent assigned a pending task it never claimed must be
    able to unclaim it (escape the pending-assigned trap). The row stays
    pending; only the assignment is released so the dispatcher can
    reassign it instead of orphaning the task + looping the agent."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    # status is PENDING, never claimed — the smoke-17 trap scenario.
    out = await svc.unclaim_for_agent(task.id, agent_id=task_setup["agent_id"])
    assert out is not None
    assert out.status == TaskStatus.PENDING
    assert out.assigned_to is None
    assert out.active_claimant_id is None


@pytest.mark.asyncio
async def test_unclaim_for_agent_returns_none_when_paused(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """A non-pending/claimed/in_progress status (e.g. paused — which has
    `resume`, not `unclaim`) is still not unclaimable."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    assert await svc.unclaim_for_agent(task.id, agent_id=task_setup["agent_id"]) is None


# ---------------------------------------------------------------------------
# resume_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_for_agent_returns_none_when_wrong_assignee(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    other = AgentTable(
        id=uuid4(),
        name="Other",
        slug=f"other-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    task.assigned_to = other.id
    await db_session.flush()
    assert await svc.resume_for_agent(task.id, agent_id=task_setup["agent_id"]) is None


@pytest.mark.asyncio
async def test_resume_for_agent_returns_none_when_not_paused(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    assert await svc.resume_for_agent(task.id, agent_id=task_setup["agent_id"]) is None


@pytest.mark.asyncio
async def test_resume_for_agent_advances_to_in_progress(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    out = await svc.resume_for_agent(task.id, agent_id=task_setup["agent_id"])
    assert out is not None
    assert out.status == TaskStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# block / unblock with task-dependency blocker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    blocker = await svc.create(_req(task_setup))
    assert await svc.block(uuid4(), blocker_task_id=blocker.id) is None


@pytest.mark.asyncio
async def test_block_adds_to_dependencies(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    blocker = await svc.create(_req(task_setup))
    blocked = await svc.block(task.id, blocker_task_id=blocker.id)
    assert blocked is not None
    assert blocked.status == TaskStatus.BLOCKED
    assert blocker.id in blocked.dependency_ids
    assert blocked.blocker_resolver_type == BlockerResolverType.AGENT


@pytest.mark.asyncio
async def test_block_does_not_duplicate_existing_dep(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    blocker = await svc.create(_req(task_setup))
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    task.dependency_ids = [blocker.id]
    await db_session.flush()
    blocked = await svc.block(task.id, blocker_task_id=blocker.id)
    assert blocked is not None
    # Still single occurrence
    assert blocked.dependency_ids.count(blocker.id) == 1


# ---------------------------------------------------------------------------
# soft_block — full happy path including HUMAN resolver type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_block_with_human_resolver(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    blocked = await svc.soft_block(
        task.id,
        SoftBlockInfo(
            reason="HITL needed",
            blocker_type="question",
            what_needed="answer",
            resolver_type=BlockerResolverType.HUMAN,
        ),
    )
    assert blocked is not None
    assert blocked.status == TaskStatus.BLOCKED
    assert blocked.blocker_resolver_type == BlockerResolverType.HUMAN


@pytest.mark.asyncio
async def test_soft_block_returns_none_when_not_in_progress(
    task_setup: dict,
) -> None:
    """soft_block requires IN_PROGRESS — PENDING task fails the gate."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    out = await svc.soft_block(
        task.id,
        SoftBlockInfo(reason="x", blocker_type="ext", what_needed="y"),
    )
    assert out is None


# ---------------------------------------------------------------------------
# unblock — when not BLOCKED returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unblock_returns_none_when_not_blocked(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    assert await svc.unblock(task.id) is None


# ---------------------------------------------------------------------------
# pause/resume happy paths verifying side-effects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_in_progress_works(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    out = await svc.pause(task.id)
    assert out is not None
    assert out.status == TaskStatus.PAUSED


@pytest.mark.asyncio
async def test_resume_paused_works(task_setup: dict, db_session: AsyncSession) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    await db_session.flush()
    out = await svc.resume(task.id)
    assert out is not None
    assert out.status == TaskStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# fail_qa: original developer reassignment & no-original-developer fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_qa_reassigns_to_original_developer(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    dev_id = task_setup["agent_id"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.orchestration_markers = {"original_developer": str(dev_id)}
    await db_session.flush()
    failed = await svc.fail_qa(task.id, notes="missing tests")
    assert failed is not None
    assert failed.status == TaskStatus.NEEDS_REVISION
    assert failed.assigned_to == dev_id


@pytest.mark.asyncio
async def test_fail_qa_increments_revision_count(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Each QA bounce to needs_revision increments the O(1) rework counter."""
    svc = task_setup["svc"]
    dev_id = task_setup["agent_id"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.orchestration_markers = {"original_developer": str(dev_id)}
    await db_session.flush()
    assert task.revision_count == 0

    failed = await svc.fail_qa(task.id, notes="missing tests")
    assert failed is not None
    assert failed.revision_count == 1
    count_after_first = failed.revision_count

    # A second QA cycle bumps it again (once per transition into needs_revision).
    failed.status = TaskStatus.AWAITING_QA
    await db_session.flush()
    again = await svc.fail_qa(task.id, notes="still missing")
    assert again is not None
    assert again.revision_count == count_after_first + 1


@pytest.mark.asyncio
async def test_fail_qa_with_no_original_dev_unassigns(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    # No quick_context — extract_original_developer returns None
    await db_session.flush()
    failed = await svc.fail_qa(task.id, notes="needs more")
    assert failed is not None
    assert failed.assigned_to is None


@pytest.mark.asyncio
async def test_fail_qa_routes_to_dev_via_work_session_when_marker_missing(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """A dev task in needs_revision with a MISSING original_developer marker
    routes back to the developer who worked it (resolved from the work
    session), NOT to the pool.

    Unassigning sends the task to the pool where a cell PM (PMs can re-claim
    needs_revision) grabs it — the live 2026-06-27 "needs revision on a dev
    task sent to the cell PM" bug. The marker is the fast path; the work
    session is the load-bearing fallback (the marker is unreliable in
    practice). The fallback also self-heals the marker so a subsequent
    re-fail takes the fast path.
    """
    svc = task_setup["svc"]
    dev_id = task_setup["agent_id"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/abc"
    await db_session.flush()

    # The dev worked the task (a work session exists) but the marker was never
    # set — e.g. the task was rerouted before submit_for_qa ran, or the marker
    # failed to persist (live observation). This is the "OG DEV NEVER
    # PERSISTED" scenario.
    ws = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=dev_id,
        branch_name="feature/backend/abc",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
    )
    db_session.add(ws)

    # A separate QA agent claims and fails the task.
    qa = AgentTable(
        id=uuid4(),
        name="QA",
        slug=f"be-qa-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(qa)
    await db_session.flush()

    task.status = TaskStatus.AWAITING_QA
    task.assigned_to = qa.id
    task.claimed_by = qa.id
    # No orchestration_markers — extract_original_developer returns None.
    await db_session.flush()

    failed = await svc.fail_qa(task.id, notes="needs more")
    assert failed is not None
    assert failed.status == TaskStatus.NEEDS_REVISION
    # Routed back to the dev — NOT unassigned (pool, where a cell PM would
    # claim it) and NOT left on the QA.
    assert failed.assigned_to == dev_id
    assert failed.claimed_by == dev_id
    # Self-healed: the marker is now stamped so the next fail_qa uses the
    # fast path and the QA-review index attributes the work correctly.
    assert markers.get_original_developer(failed) == str(dev_id)


@pytest.mark.asyncio
async def test_create_subtask_round_trips_collision_surfaces_and_deps(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """create_subtask forwards intends_to_touch / adds_migration /
    touches_shared / sequence / dependency_ids onto the created subtask.

    Previously these were dropped inside create_subtask's `prepared`
    TaskCreateRequest, so a dev task delegated with a collision surface or an
    explicit dependency lost it before persistence — the root cause of
    dev-task dependency_ids always being [] (the live 2026-06-27 out-of-order
    break). The base ``create`` persists them (task.py:878-884); this test
    locks the forwarding through create_subtask.
    """
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    await db_session.flush()
    dep_id = uuid4()
    sub = await svc.create_subtask(
        TaskCreateRequest(
            title="child",
            description="child description with enough length",
            acceptance_criteria=["ac1"],
            team=Team.BACKEND,
            created_by=task_setup["agent_id"],
            project_id=task_setup["project_id"],
            parent_task_id=parent.id,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
            sequence=3,
            dependency_ids=[dep_id],
            intends_to_touch=["roboco/services/foo.py"],
            adds_migration=True,
            touches_shared=True,
        )
    )
    assert sub.intends_to_touch == ["roboco/services/foo.py"]
    assert sub.adds_migration is True
    assert sub.touches_shared is True
    expected_sequence = 3
    assert sub.sequence == expected_sequence
    assert sub.dependency_ids == [dep_id]


@pytest.mark.asyncio
async def test_wire_sibling_collision_dag_serializes_overlapping_dev_tasks(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """wire_sibling_collision_dag runs the collision analyzer over a parent's
    surfaced dev-task siblings and wires dependency_ids so a later dev task
    whose surface overlaps an earlier one stays PENDING until it completes.

    T1 (a.py) and T2 (b.py) are disjoint -> parallel (no edge). T3 (a.py)
    overlaps T1 -> T3 depends-on T1. The explicit `depends_on` override on T3
    (forwarded through create_subtask in S1) is also present."""
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    await db_session.flush()

    async def _dev(seq: int, surface: list[str]) -> Any:
        t = await svc.create_subtask(
            TaskCreateRequest(
                title=f"dev-{seq}",
                description=f"dev task {seq} description long enough",
                acceptance_criteria=["ac"],
                team=Team.BACKEND,
                created_by=task_setup["agent_id"],
                project_id=task_setup["project_id"],
                parent_task_id=parent.id,
                task_type=TaskType.CODE,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.MEDIUM,
                sequence=seq,
                intends_to_touch=surface,
            )
        )
        await svc.set_sequence(t.id, seq)
        return t

    t1 = await _dev(0, ["roboco/api/a.py"])
    t2 = await _dev(1, ["roboco/api/b.py"])
    t3 = await _dev(2, ["roboco/api/a.py"])

    await svc.wire_sibling_collision_dag(parent.id)

    # Reload to read the wired dependency_ids.
    r1 = await svc.get(t1.id)
    r2 = await svc.get(t2.id)
    r3 = await svc.get(t3.id)
    assert r1 is not None and r2 is not None and r3 is not None
    # T1 and T2 are disjoint -> parallel (no collision edge between them).
    assert r1.dependency_ids == []
    assert r2.dependency_ids == []
    # T3 overlaps T1 (same file, same repo) -> T3 depends-on T1.
    assert t1.id in r3.dependency_ids
    # T2 does NOT collide with T3 (disjoint file) -> no T2->T3 edge.
    assert t2.id not in r3.dependency_ids


@pytest.mark.asyncio
async def test_wire_sibling_collision_dag_chains_undeclared_same_assignee_lane(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Undeclared-surface fallback: two dev siblings with NO collision surface
    on the same assignee + same repo are chained by sequence so the later one
    waits for the earlier — the live out-of-order start (a dev with an unmerged
    earlier task starting the next one) is prevented at wiring time. Cross-dev
    siblings stay parallel (the lane is same-assignee scoped)."""
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    await db_session.flush()
    other_dev = AgentTable(
        id=uuid4(),
        name="Dev2",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other_dev)
    await db_session.flush()

    async def _dev(seq: int, assignee: UUID) -> Any:
        t = await svc.create_subtask(
            TaskCreateRequest(
                title=f"dev-{seq}",
                description=f"dev task {seq} description long enough",
                acceptance_criteria=["ac"],
                team=Team.BACKEND,
                created_by=task_setup["agent_id"],
                project_id=task_setup["project_id"],
                parent_task_id=parent.id,
                task_type=TaskType.CODE,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.MEDIUM,
                sequence=seq,
                assigned_to=assignee,
            )
        )
        await svc.set_sequence(t.id, seq)
        return t

    # Same assignee, no declared surface -> fallback chains seq-1 behind seq-0.
    a = await _dev(0, task_setup["agent_id"])
    b = await _dev(1, task_setup["agent_id"])
    # Different assignee, no declared surface -> parallel (no fallback edge).
    c = await _dev(2, cast("UUID", other_dev.id))

    await svc.wire_sibling_collision_dag(parent.id)

    ra = await svc.get(a.id)
    rb = await svc.get(b.id)
    rc = await svc.get(c.id)
    assert ra is not None and rb is not None and rc is not None
    # Earlier sibling leads the lane (no incoming edge).
    assert ra.dependency_ids == []
    # Later same-assignee sibling waits on the earlier one.
    assert a.id in rb.dependency_ids
    # Cross-dev sibling is not chained onto the first dev's lane.
    assert a.id not in rc.dependency_ids
    assert b.id not in rc.dependency_ids


@pytest.mark.asyncio
async def test_wire_cell_task_wave_chain_chains_to_predecessor_cell_tasks(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Kind 2: a cell-task under root-subtask R1 depends on every cell-task
    under R0, where R1 depends-on R0 (the kind-1 wave-chain edge on R1).

    A root-subtask may fan to several cell-tasks (different cells) — both of
    R0's cell-tasks are wired onto R1's cell-task so its branch carries the
    whole previous wave's merged cell work. Idempotent (add_dependency dedupes).
    """
    svc = task_setup["svc"]
    # Two root-subtasks; R1 depends-on R0 (the kind-1 edge lives on R1).
    r0 = await svc.create(
        _req(
            task_setup,
            title="r0",
            team=Team.MAIN_PM,
            task_type=TaskType.PLANNING,
            nature=TaskNature.TECHNICAL,
        )
    )
    r1 = await svc.create(
        _req(
            task_setup,
            title="r1",
            team=Team.MAIN_PM,
            task_type=TaskType.PLANNING,
            nature=TaskNature.TECHNICAL,
            dependency_ids=[UUID(str(r0.id))],
        )
    )
    await db_session.flush()

    async def _cell(parent_id: UUID, team: Team) -> Any:
        return await svc.create_subtask(
            TaskCreateRequest(
                title=f"ct-{team.value}",
                description="cell task description long enough",
                acceptance_criteria=["ac"],
                team=team,
                created_by=task_setup["agent_id"],
                project_id=task_setup["project_id"],
                parent_task_id=parent_id,
                task_type=TaskType.CODE,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.MEDIUM,
            )
        )

    # R0 fans to two cell-tasks (backend + frontend — the cross-cell fanout the
    # CEO confirmed a root-subtask may carry).
    ct0a = await _cell(UUID(str(r0.id)), Team.BACKEND)
    ct0b = await _cell(UUID(str(r0.id)), Team.FRONTEND)
    # R1's cell-task.
    ct1 = await _cell(UUID(str(r1.id)), Team.BACKEND)

    await svc.wire_cell_task_wave_chain(ct1.id)
    # Idempotent: a second run adds no duplicates.
    await svc.wire_cell_task_wave_chain(ct1.id)

    r1ct = await svc.get(ct1.id)
    assert r1ct is not None
    # ct1 depends on BOTH of R0's cell-tasks (the whole previous wave's set).
    assert UUID(str(ct0a.id)) in r1ct.dependency_ids
    assert UUID(str(ct0b.id)) in r1ct.dependency_ids
    # Idempotency: each predecessor appears once.
    assert r1ct.dependency_ids.count(UUID(str(ct0a.id))) == 1


@pytest.mark.asyncio
async def test_wire_by_osmosis_edge_first_dev_task_depends_on_prev_wave_tail(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Kind 4: the first dev task (sequence 0) of a cell-task under R1 depends
    on the tail (highest-sequence) dev task of R0's cell-task. A non-first dev
    task (sequence > 0) gets no by-osmosis edge (it inherits the tail via the
    kind-3 collision DAG or shares the merged base)."""
    svc = task_setup["svc"]
    r0 = await svc.create(
        _req(
            task_setup,
            title="r0",
            team=Team.MAIN_PM,
            task_type=TaskType.PLANNING,
            nature=TaskNature.TECHNICAL,
        )
    )
    r1 = await svc.create(
        _req(
            task_setup,
            title="r1",
            team=Team.MAIN_PM,
            task_type=TaskType.PLANNING,
            nature=TaskNature.TECHNICAL,
            dependency_ids=[UUID(str(r0.id))],
        )
    )
    await db_session.flush()

    async def _sub(parent_id: UUID, seq: int) -> Any:
        t = await svc.create_subtask(
            TaskCreateRequest(
                title=f"t{seq}",
                description=f"subtask {seq} description long enough",
                acceptance_criteria=["ac"],
                team=Team.BACKEND,
                created_by=task_setup["agent_id"],
                project_id=task_setup["project_id"],
                parent_task_id=parent_id,
                task_type=TaskType.CODE,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.MEDIUM,
                sequence=seq,
            )
        )
        await svc.set_sequence(t.id, seq)
        return t

    # R0's cell-task with two dev tasks; tail = sequence 1.
    ct0 = await _sub(UUID(str(r0.id)), 0)
    d0a = await _sub(UUID(str(ct0.id)), 0)
    d0b = await _sub(UUID(str(ct0.id)), 1)  # tail
    # R1's cell-task with the first dev task (sequence 0) + a later one.
    ct1 = await _sub(UUID(str(r1.id)), 0)
    first = await _sub(UUID(str(ct1.id)), 0)
    second = await _sub(UUID(str(ct1.id)), 1)

    await svc.wire_by_osmosis_edge(first.id)
    await svc.wire_by_osmosis_edge(second.id)

    rf = await svc.get(first.id)
    rs = await svc.get(second.id)
    assert rf is not None and rs is not None
    # The first dev task carries the by-osmosis edge to R0's cell-task's TAIL
    # (d0b, sequence 1) — not the non-tail d0a.
    assert UUID(str(d0b.id)) in rf.dependency_ids
    assert UUID(str(d0a.id)) not in rf.dependency_ids
    # The second dev task (sequence 1) gets NO by-osmosis edge.
    assert UUID(str(d0b.id)) not in rs.dependency_ids


@pytest.mark.asyncio
async def test_fail_qa_work_session_fallback_excludes_qa_session(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """The work-session fallback must not hand the task back to the QA.

    If the only developer work session were the QA's (it isn't — QA sessions
    are skipped at creation — but defensively the exclude filter must keep a
    QA-attributed session from being misread as the revision dev), the
    fallback would loop the task back to the reviewer. The exclude filter
    (qa_agent_id) guarantees only a real developer is resolved.
    """
    svc = task_setup["svc"]
    dev_id = task_setup["agent_id"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/abc"
    await db_session.flush()

    qa = AgentTable(
        id=uuid4(),
        name="QA",
        slug=f"be-qa-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(qa)
    await db_session.flush()

    # A QA-attributed work session (older, abandoned) and a dev-attributed one
    # (newer, active). Only one ACTIVE session may exist per task
    # (``uq_work_sessions_one_active_per_task``), so the QA session is
    # ABANDONED — realistic for a stale QA review session and still in the
    # fallback query's result set (the query filters by task_id + agent_id,
    # not by status). The exclude filter (``agent_id != qa_agent_id``) is
    # what must keep the QA session from being misread as the revision dev.
    qa_ws = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=qa.id,
        branch_name="feature/backend/abc",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ABANDONED,
    )
    dev_ws = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=dev_id,
        branch_name="feature/backend/abc",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
    )
    db_session.add_all([qa_ws, dev_ws])
    await db_session.flush()

    task.status = TaskStatus.AWAITING_QA
    task.assigned_to = qa.id
    task.claimed_by = qa.id
    await db_session.flush()

    failed = await svc.fail_qa(task.id, notes="needs more")
    assert failed is not None
    # Resolved to the dev, NOT the QA — the exclude filter did its job.
    assert failed.assigned_to == dev_id
    assert failed.assigned_to != qa.id


# ---------------------------------------------------------------------------
# ceo_approve / ceo_reject — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ceo_approve_returns_none_when_wrong_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    assert await svc.ceo_approve(task.id) is None


@pytest.mark.asyncio
async def test_ceo_approve_marks_completed(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    task.pr_number = 1
    task.pr_url = "u"
    task.docs_complete = True
    task.pr_created = True
    await db_session.flush()
    approved = await svc.ceo_approve(task.id, notes="approved")
    assert approved is not None
    assert approved.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_ceo_reject_returns_none_when_wrong_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    assert await svc.ceo_reject(task.id, reason="not aligned") is None


@pytest.mark.asyncio
async def test_ceo_reject_reassigns_to_original_dev(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    dev_id = task_setup["agent_id"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    task.orchestration_markers = {"original_developer": str(dev_id)}
    await db_session.flush()
    rejected = await svc.ceo_reject(task.id, reason="re-do auth flow")
    assert rejected is not None
    assert rejected.status == TaskStatus.NEEDS_REVISION
    assert rejected.assigned_to == dev_id


@pytest.mark.asyncio
async def test_ceo_reject_clears_assignment_when_no_original_dev(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    task.assigned_to = task_setup["agent_id"]
    # No quick_context — original_dev resolves None
    await db_session.flush()
    rejected = await svc.ceo_reject(task.id, reason="redo")
    assert rejected is not None
    assert rejected.assigned_to is None


@pytest.mark.asyncio
async def test_ceo_reject_routes_coordination_task_to_main_pm(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """A rejected coordination/integration root goes to the Main PM to delegate,
    not back to a developer."""
    svc = task_setup["svc"]
    main_pm_id = UUID(AGENT_UUIDS["main-pm"])
    if await db_session.get(AgentTable, main_pm_id) is None:
        db_session.add(
            AgentTable(
                id=main_pm_id,
                name="Main PM",
                slug="main-pm",
                role=AgentRole.MAIN_PM,
                team=Team.MAIN_PM,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="pm",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
    # The CEO rejects the task; ceo_reject emits an audit row keyed to the CEO
    # agent, so the CEO row must exist (fk_audit_log_agent_id_agents).
    ceo_id = UUID(AGENT_UUIDS["ceo"])
    if await db_session.get(AgentTable, ceo_id) is None:
        db_session.add(
            AgentTable(
                id=ceo_id,
                name="CEO",
                slug="ceo",
                role=AgentRole.CEO,
                team=Team.MAIN_PM,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="ceo",
                capabilities=[],
                permissions=[],
                metrics={},
            )
        )
    product = ProductTable(
        name="P", slug=f"p-{uuid4().hex[:8]}", created_by=task_setup["agent_id"]
    )
    db_session.add(product)
    await db_session.flush()

    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    task.project_id = None  # coordination root: no project, has product
    task.product_id = product.id
    await db_session.flush()

    rejected = await svc.ceo_reject(task.id, reason="redo the API contract")
    assert rejected is not None
    # A coordination root goes to PENDING (the Main PM's claim source), NOT
    # needs_revision — that status is developer-claim-only and would deadlock
    # the Main PM, which owns the root and must re-plan/re-delegate.
    assert rejected.status == TaskStatus.PENDING
    assert rejected.team == Team.MAIN_PM
    assert rejected.assigned_to == main_pm_id
    assert rejected.claimed_by is None


@pytest.mark.asyncio
async def test_ceo_reject_routes_batch_umbrella_to_main_pm(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """A rejected MegaTask umbrella (batch_id, top-level, no project/product) is a
    branchless coordination root too — it routes to the Main PM to re-plan, never
    to a developer (needs_revision would deadlock the Main PM that owns it)."""
    svc = task_setup["svc"]
    main_pm_id = UUID(AGENT_UUIDS["main-pm"])
    if await db_session.get(AgentTable, main_pm_id) is None:
        db_session.add(
            AgentTable(
                id=main_pm_id,
                name="Main PM",
                slug="main-pm",
                role=AgentRole.MAIN_PM,
                team=Team.MAIN_PM,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="pm",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
        await db_session.flush()

    # The CEO rejects the umbrella; ceo_reject emits an audit row keyed to the
    # CEO agent, so the CEO row must exist (fk_audit_log_agent_id_agents).
    ceo_id = UUID(AGENT_UUIDS["ceo"])
    if await db_session.get(AgentTable, ceo_id) is None:
        db_session.add(
            AgentTable(
                id=ceo_id,
                name="CEO",
                slug="ceo",
                role=AgentRole.CEO,
                team=Team.MAIN_PM,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="ceo",
                capabilities=[],
                permissions=[],
                metrics={},
            )
        )
        await db_session.flush()

    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    task.project_id = None  # umbrella: no project, no product — carries a batch_id
    task.product_id = None
    task.batch_id = uuid4()
    task.parent_task_id = None
    await db_session.flush()

    rejected = await svc.ceo_reject(task.id, reason="re-sequence the waves")
    assert rejected is not None
    assert rejected.status == TaskStatus.PENDING
    assert rejected.team == Team.MAIN_PM
    assert rejected.assigned_to == main_pm_id
    assert rejected.claimed_by is None


@pytest.mark.asyncio
async def test_ceo_reject_writes_handoff_journal(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """The CEO's reason is recorded as a DECISION_LOG journal entry on the task —
    the channel that actually reaches the reworker (quick_context does not)."""
    svc = task_setup["svc"]
    ceo_id = UUID(AGENT_UUIDS["ceo"])
    if await db_session.get(AgentTable, ceo_id) is None:
        db_session.add(
            AgentTable(
                id=ceo_id,
                name="CEO",
                slug="ceo",
                role=AgentRole.CEO,
                team=Team.MAIN_PM,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="ceo",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
        await db_session.flush()

    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    task.orchestration_markers = {"original_developer": str(task_setup["agent_id"])}
    await db_session.flush()

    reason = "AC9/AC10 totals must include cache tokens"
    rejected = await svc.ceo_reject(task.id, reason=reason)
    assert rejected is not None

    entries = (
        (
            await db_session.execute(
                select(JournalEntryTable).where(
                    JournalEntryTable.task_id == task.id,
                    JournalEntryTable.type == JournalEntryType.DECISION_LOG,
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(reason in (e.content or "") for e in entries)


# ---------------------------------------------------------------------------
# escalate_to_ceo - all error branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_to_ceo_returns_none_when_wrong_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    out = await svc.escalate_to_ceo(task.id, agent_role="cell_pm", notes="x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_to_ceo_returns_none_for_subtask(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    sub = await svc.create(_req(task_setup, parent_task_id=parent.id))
    sub.status = TaskStatus.AWAITING_PM_REVIEW
    sub.pr_number = 1
    await db_session.flush()
    out = await svc.escalate_to_ceo(sub.id, agent_role="cell_pm", notes="x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_to_ceo_returns_none_when_no_pr(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = None
    await db_session.flush()
    out = await svc.escalate_to_ceo(task.id, agent_role="main_pm", notes="x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_to_ceo_waives_pr_for_batch_umbrella(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """A MegaTask umbrella is branchless and assembles no PR — escalate_to_ceo
    must NOT block it on a missing pr_number, or umbrella completion crashes."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.project_id = None  # umbrella: branchless, no repo, no PR
    task.product_id = None
    task.batch_id = uuid4()
    task.parent_task_id = None
    task.pr_number = None
    await db_session.flush()
    escalated = await svc.escalate_to_ceo(
        task.id, agent_role="main_pm", notes="MegaTask ready for CEO sign-off"
    )
    assert escalated is not None
    assert escalated.status == TaskStatus.AWAITING_CEO_APPROVAL


@pytest.mark.asyncio
async def test_escalate_to_ceo_advances_status_with_notes(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = 42
    task.pr_url = "https://example.com/pr/42"
    task.pr_created = True
    task.docs_complete = True
    await db_session.flush()
    escalated = await svc.escalate_to_ceo(
        task.id, agent_role="main_pm", notes="needs CEO review for breaking change"
    )
    assert escalated is not None
    assert escalated.status == TaskStatus.AWAITING_CEO_APPROVAL
    # The escalation note is a structured marker now, not quick_context soup.
    assert (
        markers.get_transition_note(escalated, "escalate_to_ceo")
        == "needs CEO review for breaking change"
    )


# ---------------------------------------------------------------------------
# cancel + cascade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_skips_already_terminal_descendants(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Descendants already in COMPLETED/CANCELLED are skipped, not re-cancelled."""
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    completed_child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    completed_child.status = TaskStatus.COMPLETED
    pending_child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()
    out = await svc.cancel(parent.id, agent_role="cell_pm")
    assert out is not None
    refreshed_completed = await svc.get(completed_child.id)
    assert refreshed_completed is not None
    assert refreshed_completed.status == TaskStatus.COMPLETED
    refreshed_pending = await svc.get(pending_child.id)
    assert refreshed_pending is not None
    assert refreshed_pending.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_returns_task_with_cancellation_note_appended(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.dev_notes = "earlier note"
    out = await svc.cancel(task.id, cancellation_note="duplicate")
    assert out is not None
    assert "duplicate" in (out.dev_notes or "")


# ---------------------------------------------------------------------------
# _unblock_dependents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unblock_dependents_clears_dep_id(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Completing a blocker unblocks its dependents."""
    svc = task_setup["svc"]
    blocker = await svc.create(_req(task_setup))
    dependent = await svc.create(_req(task_setup))
    dependent.status = TaskStatus.BLOCKED
    dependent.dependency_ids = [blocker.id]
    await db_session.flush()
    await svc._unblock_dependents(blocker.id)
    refreshed = await svc.get(dependent.id)
    assert refreshed is not None
    assert blocker.id not in refreshed.dependency_ids
    # The cleared dependency is remembered so the unblock briefing can surface it.
    assert blocker.id in refreshed.completed_dependency_ids
    assert refreshed.status == TaskStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_unblock_dependents_keeps_blocked_when_other_deps_remain(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    blocker = await svc.create(_req(task_setup))
    other_blocker = await svc.create(_req(task_setup))
    dependent = await svc.create(_req(task_setup))
    dependent.status = TaskStatus.BLOCKED
    dependent.dependency_ids = [blocker.id, other_blocker.id]
    await db_session.flush()
    await svc._unblock_dependents(blocker.id)
    refreshed = await svc.get(dependent.id)
    assert refreshed is not None
    # Still blocked because other_blocker is still in deps
    assert refreshed.status == TaskStatus.BLOCKED
    # but the one dependency that did complete is recorded.
    assert blocker.id in refreshed.completed_dependency_ids
    assert other_blocker.id not in refreshed.completed_dependency_ids


# ---------------------------------------------------------------------------
# claim — gate validations (team mismatch, role mismatch, self-review)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_rejects_when_agent_team_mismatch(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    # Create an agent on a different team
    other = AgentTable(
        id=uuid4(),
        name="FE",
        slug=f"fe-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.FRONTEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    task = await svc.create(_req(task_setup, team=Team.BACKEND))
    task.branch_name = "feature/backend/x"
    await db_session.flush()
    out = await svc.claim(task.id, other.id)
    assert out is None


@pytest.mark.asyncio
async def test_claim_rejects_self_review_for_qa(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    qa_agent = AgentTable(
        id=uuid4(),
        name="QA",
        slug=f"be-qa-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(qa_agent)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.branch_name = "feature/backend/x"
    task.orchestration_markers = {"original_developer": str(qa_agent.id)}
    await db_session.flush()
    out = await svc.claim(task.id, qa_agent.id)
    assert out is None


@pytest.mark.asyncio
async def test_claim_management_role_can_claim_any_team(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    main_pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(main_pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup, team=Team.BACKEND))
    task.branch_name = "feature/backend/x"
    await db_session.flush()
    claimed = await svc.claim(task.id, main_pm.id)
    assert claimed is not None
    assert claimed.assigned_to == main_pm.id


# ---------------------------------------------------------------------------
# pass_qa with notes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_qa_records_notes(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.pr_number = 1
    task.pr_url = "u"
    await db_session.flush()
    passed = await svc.pass_qa(task.id, notes="LGTM detailed", agent_role="qa")
    assert passed is not None
    assert passed.qa_notes == "LGTM detailed"


@pytest.mark.asyncio
async def test_pass_qa_resets_docs_complete(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Passing QA forces docs_complete=False so the documenter must redo."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.pr_number = 1
    task.pr_url = "u"
    task.docs_complete = True
    await db_session.flush()
    passed = await svc.pass_qa(task.id, agent_role="qa")
    assert passed is not None
    assert passed.docs_complete is False


# ---------------------------------------------------------------------------
# claim_seeds_branch via auto-create — failure rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_rolls_back_on_branch_failure(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _ensure_branch_for_task fails, claim fields revert."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    # No branch — claim will try to auto-create.
    await db_session.flush()

    async def _fail(*_args: object, **_kwargs: object) -> str:
        raise ValueError("simulated branch failure")

    monkeypatch.setattr(svc, "_ensure_branch_for_task", _fail)
    with pytest.raises(ValueError, match="simulated branch failure"):
        await svc.claim(task.id, task_setup["agent_id"])

    refreshed = await svc.get(task.id)
    assert refreshed is not None
    # Status rolled back to PENDING
    assert refreshed.status == TaskStatus.PENDING
    assert refreshed.assigned_to is None


# ---------------------------------------------------------------------------
# submit_for_pm_review — full happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_for_pm_review_returns_none_when_not_in_progress(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    out = await svc.submit_for_pm_review(task.id, agent_role="cell_pm")
    assert out is None


@pytest.mark.asyncio
async def test_submit_for_pm_review_returns_none_when_no_branch(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.branch_name = None
    await db_session.flush()
    out = await svc.submit_for_pm_review(task.id, agent_role="cell_pm")
    assert out is None


@pytest.mark.asyncio
async def test_submit_for_pm_review_returns_none_when_no_pr(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.branch_name = "feature/backend/x"
    task.pr_created = False
    await db_session.flush()
    out = await svc.submit_for_pm_review(task.id, agent_role="cell_pm")
    assert out is None


@pytest.mark.asyncio
async def test_submit_for_pm_review_returns_none_when_active_descendants(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    parent.status = TaskStatus.IN_PROGRESS
    parent.branch_name = "feature/backend/x"
    parent.pr_created = True
    parent.pr_number = 1
    await db_session.flush()
    # Create a non-terminal child
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()
    assert child is not None  # ensure child exists
    out = await svc.submit_for_pm_review(parent.id, agent_role="cell_pm")
    assert out is None


@pytest.mark.asyncio
async def test_submit_for_pm_review_advances_with_notes(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.branch_name = "feature/backend/x"
    task.pr_created = True
    task.pr_number = 1
    task.docs_complete = True
    await db_session.flush()
    out = await svc.submit_for_pm_review(
        task.id, agent_role="cell_pm", notes="ready for review"
    )
    assert out is not None
    assert out.status == TaskStatus.AWAITING_PM_REVIEW


@pytest.mark.asyncio
async def test_submit_for_pm_review_waives_branch_pr_for_batch_umbrella(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """A MegaTask umbrella is branchless by design yet must walk
    in_progress -> awaiting_pm_review; submit_for_pm_review waives the
    branch+PR requirement for a batch umbrella so completion does not deadlock."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.project_id = None  # umbrella: branchless, no repo, no PR
    task.product_id = None
    task.batch_id = uuid4()
    task.parent_task_id = None  # umbrella is top-level
    task.branch_name = None
    task.pr_created = False
    task.pr_number = None
    await db_session.flush()
    out = await svc.submit_for_pm_review(task.id, agent_role="main_pm")
    assert out is not None
    assert out.status == TaskStatus.AWAITING_PM_REVIEW


@pytest.mark.asyncio
async def test_activate_batch_root_subtasks_retypes_code_to_planning(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """A board-routed MegaTask root-subtask is created in BACKLOG with
    task_type=code; _activate_batch_root_subtasks must retype it code->planning
    when flipping team to main_pm, mirroring approve_and_start, or the
    main_pm+code combo recurs."""
    svc = task_setup["svc"]
    # approve_and_start resolves the main-pm agent by slug — seed it.
    main_pm = AgentTable(
        id=uuid4(),
        name="Main PM",
        slug="main-pm",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="main-pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(main_pm)
    await db_session.flush()

    batch = uuid4()
    # Umbrella: board-routed, pending, board review complete, branchless.
    umbrella = await svc.create(_req(task_setup, title="umbrella"))
    umbrella.status = TaskStatus.PENDING
    umbrella.team = cast("Any", Team.BOARD)
    umbrella.board_review_complete = True
    umbrella.project_id = None
    umbrella.product_id = None
    umbrella.batch_id = batch
    umbrella.parent_task_id = None
    umbrella.task_type = cast("Any", TaskType.PLANNING)
    await db_session.flush()

    # Root-subtask: held in BACKLOG on the board, code-typed (the danger case).
    child = await svc.create(
        _req(task_setup, title="root-sub", parent_task_id=umbrella.id)
    )
    child.status = TaskStatus.BACKLOG
    child.team = cast("Any", Team.BOARD)
    child.batch_id = batch
    child.parent_task_id = umbrella.id
    child.task_type = cast("Any", TaskType.CODE)
    await db_session.flush()

    approved = await svc.approve_and_start(umbrella.id)
    assert approved is not None

    refreshed = await svc.get(child.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PENDING
    assert refreshed.team == Team.MAIN_PM
    # The fix: a main_pm team may not own a code task -> retype to planning.
    assert refreshed.task_type == TaskType.PLANNING


# ---------------------------------------------------------------------------
# docs_complete: full path including descendants check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_complete_returns_none_when_active_descendants(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    parent.status = TaskStatus.AWAITING_DOCUMENTATION
    parent.assigned_to = task_setup["agent_id"]
    parent.pr_number = 1
    parent.pr_url = "u"
    await db_session.flush()
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()
    assert child is not None
    out = await svc.docs_complete(parent.id, doc_notes="docs done")
    assert out is None


@pytest.mark.asyncio
async def test_docs_complete_returns_none_when_invalid_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    out = await svc.docs_complete(task.id, doc_notes="docs")
    assert out is None


@pytest.mark.asyncio
async def test_docs_complete_advances_when_pr_already_created(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_DOCUMENTATION
    task.assigned_to = task_setup["agent_id"]
    task.pr_number = 1
    task.pr_url = "u"
    task.pr_created = True
    await db_session.flush()
    out = await svc.docs_complete(task.id, doc_notes="documented all flows")
    assert out is not None
    assert out.docs_complete is True
    # Both flags now true → advances to AWAITING_PM_REVIEW
    assert out.status == TaskStatus.AWAITING_PM_REVIEW


# ---------------------------------------------------------------------------
# mark_pr_created edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_pr_created_when_docs_not_complete_keeps_status(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_DOCUMENTATION
    task.assigned_to = task_setup["agent_id"]
    task.docs_complete = False
    await db_session.flush()
    pr_num = 10
    out = await svc.mark_pr_created(task.id, pr_number=pr_num, pr_url="u10")
    assert out is not None
    # PR set, but stays in awaiting_documentation
    assert out.pr_number == pr_num
    assert out.status == TaskStatus.AWAITING_DOCUMENTATION


# ---------------------------------------------------------------------------
# complete — happy path & edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_returns_none_when_invalid_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    out = await svc.complete(task.id, agent_id=task_setup["agent_id"])
    assert out is None


@pytest.mark.asyncio
async def test_complete_returns_none_when_active_descendants(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    parent.status = TaskStatus.AWAITING_PM_REVIEW
    await db_session.flush()
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()
    assert child is not None
    out = await svc.complete(parent.id, agent_id=task_setup["agent_id"])
    assert out is None


@pytest.mark.asyncio
async def test_complete_in_progress_for_own_task(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """PM completes their own in_progress task (not awaiting_pm_review)."""
    svc = task_setup["svc"]
    pm_agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm_agent)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = pm_agent.id
    await db_session.flush()
    out = await svc.complete(task.id, agent_id=pm_agent.id)
    assert out is not None
    assert out.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_complete_cell_pm_does_not_escalate_to_main_pm(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """#178: cell_pm completing an awaiting_pm_review non-root task
    transitions it to COMPLETED and does NOT reassign to main_pm.

    Pre-#178 the cell_pm branch of ``_apply_complete_approval_chain``
    reassigned every awaiting_pm_review task to main_pm and kept it in
    awaiting_pm_review for a second-tier review — but the gateway's
    ``main_pm_complete`` rejects every non-root task
    (``parent_task_id IS NOT NULL`` → invalid_state), so main_pm had
    no verb to advance it. Result: the leaf was permanently wedged
    (observed end-to-end this session). The fix removes the cell_pm
    escalation branch; cell PM now completes non-root tasks directly,
    and cell→main escalation, when intended, uses ``submit_up``.
    """
    svc = task_setup["svc"]
    cell_pm = AgentTable(
        id=uuid4(),
        name="CellPM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    main_pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add_all([cell_pm, main_pm])
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.assigned_to = cell_pm.id
    await db_session.flush()
    out = await svc.complete(task.id, agent_id=cell_pm.id)
    assert out is not None
    assert out.status == TaskStatus.COMPLETED
    assert out.assigned_to != main_pm.id


@pytest.mark.asyncio
async def test_complete_cell_pm_no_main_pm_completes(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """#178: cell_pm completing awaiting_pm_review transitions to
    COMPLETED regardless of whether a Main PM exists. (Pre-#178 this
    test guarded the "no Main PM → escalation returns None → falls
    through to completion" fallback; post-#178 the cell_pm escalation
    branch is gone entirely, so this path is the only path.)
    """
    svc = task_setup["svc"]
    cell_pm = AgentTable(
        id=uuid4(),
        name="CellPM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(cell_pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    await db_session.flush()
    out = await svc.complete(task.id, agent_id=cell_pm.id)
    assert out is not None
    # No Main PM in scope — escalation returned None, falls through to completion
    assert out.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_complete_with_force_with_cancelled_succeeds(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """force_with_cancelled allows completion despite cancelled descendants.

    Strip any leaked Main PMs so the Cell PM completion doesn't escalate
    upward and short-circuit the cancelled-descendant code path.
    """
    svc = task_setup["svc"]
    await db_session.execute(
        cast("Table", AgentTable.__table__)
        .update()
        .where(AgentTable.role == AgentRole.MAIN_PM)
        .values(role=AgentRole.SYSTEM)
    )
    pm_agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm_agent)
    await db_session.flush()
    parent = await svc.create(_req(task_setup))
    parent.status = TaskStatus.AWAITING_PM_REVIEW
    await db_session.flush()
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    child.status = TaskStatus.CANCELLED
    await db_session.flush()
    out = await svc.complete(
        parent.id,
        agent_id=pm_agent.id,
        force_with_cancelled=True,
        justification="not needed anymore",
    )
    assert out is not None
    assert out.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_complete_without_force_with_cancelled_blocks(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Without force_with_cancelled, cancelled descendants block completion."""
    svc = task_setup["svc"]
    await db_session.execute(
        cast("Table", AgentTable.__table__)
        .update()
        .where(AgentTable.role == AgentRole.MAIN_PM)
        .values(role=AgentRole.SYSTEM)
    )
    pm_agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm_agent)
    await db_session.flush()
    parent = await svc.create(_req(task_setup))
    parent.status = TaskStatus.AWAITING_PM_REVIEW
    await db_session.flush()
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    child.status = TaskStatus.CANCELLED
    await db_session.flush()
    out = await svc.complete(parent.id, agent_id=pm_agent.id)
    assert out is None


# ---------------------------------------------------------------------------
# apply_escalation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_escalation_reassigns_and_blocks(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    target = AgentTable(
        id=uuid4(),
        name="Target",
        slug=f"target-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="t",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(target)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    await svc.apply_escalation(
        task=task,
        target_agent_id=target.id,
        escalator_slug="dev-1",
        target_slug="cell-pm",
        reason="external blocker",
    )
    assert task.status == TaskStatus.BLOCKED
    assert task.assigned_to == target.id
    assert task.blocker_raised_by == task_setup["agent_id"]
    # The escalation is a structured marker, NOT a developer note.
    assert not (task.dev_notes or "")
    esc = (task.orchestration_markers or {})["escalation"]
    assert esc == {"from": "dev-1", "to": "cell-pm", "reason": "external blocker"}


@pytest.mark.asyncio
async def test_apply_escalation_snapshots_original_owner_for_restore(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Escalation snapshots the original owner; restore returns it, not the target."""
    svc = task_setup["svc"]
    target = AgentTable(
        id=uuid4(),
        name="Target",
        slug=f"target-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="t",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(target)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    task.claimed_by = task_setup["agent_id"]
    task.status = TaskStatus.IN_PROGRESS
    task.branch_name = "feature/backend/abc12345"
    await db_session.flush()
    await svc.apply_escalation(
        task=task,
        target_agent_id=target.id,
        escalator_slug="dev-1",
        target_slug="cell-pm",
        reason="external blocker",
    )
    # The snapshot captured the outgoing dev, not the escalation target.
    assert task.pre_block_assignee == task_setup["agent_id"]
    out = await svc.unblock_with_restore(
        pm_agent_id=task_setup["agent_id"], task_id=task.id, restore=True
    )
    assert out is not None
    assert out.status == TaskStatus.IN_PROGRESS
    assert out.assigned_to == task_setup["agent_id"]
    assert out.claimed_by == task_setup["agent_id"]


# ---------------------------------------------------------------------------
# escalate / escalate_up_to_role helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_returns_none_for_missing_task(task_setup: dict) -> None:
    svc = task_setup["svc"]
    out = await svc.escalate(task_setup["agent_id"], uuid4(), reason="x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_returns_none_for_missing_agent(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    out = await svc.escalate(uuid4(), task.id, reason="x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_returns_none_when_target_slug_not_in_db(
    task_setup: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalation target slug exists in config but no AgentTable row matches."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    # Force a target slug that won't match any AgentTable row
    monkeypatch.setattr(
        "roboco.agents_config.get_escalation_target",
        lambda _slug: "nonexistent-target",
    )
    out = await svc.escalate(task_setup["agent_id"], task.id, reason="x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_succeeds_when_target_resolves(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    target = AgentTable(
        id=uuid4(),
        name="Target",
        slug=f"esc-target-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="t",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(target)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    monkeypatch.setattr(
        "roboco.agents_config.get_escalation_target",
        lambda _slug: target.slug,
    )
    out = await svc.escalate(task_setup["agent_id"], task.id, reason="bug")
    assert out is not None
    assert out.assigned_to == target.id


@pytest.mark.asyncio
async def test_escalate_up_to_role_returns_none_for_missing_task(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    out = await svc.escalate_up_to_role(task_setup["agent_id"], uuid4(), "main_pm", "x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_up_to_role_returns_none_for_missing_agent(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    out = await svc.escalate_up_to_role(uuid4(), task.id, "main_pm", "x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_up_to_role_succeeds(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    main_pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="m",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(main_pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    out = await svc.escalate_up_to_role(
        task_setup["agent_id"], task.id, "main_pm", "needs main pm"
    )
    assert out is not None


@pytest.mark.asyncio
async def test_escalate_up_to_role_returns_none_when_no_target_role(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Target role has no agents in DB.

    Strip any leaked Main PMs so escalation hits the no-target branch.
    """
    svc = task_setup["svc"]
    await db_session.execute(
        cast("Table", AgentTable.__table__)
        .update()
        .where(AgentTable.role == AgentRole.MAIN_PM)
        .values(role=AgentRole.SYSTEM)
    )
    task = await svc.create(_req(task_setup))
    await db_session.flush()
    out = await svc.escalate_up_to_role(task_setup["agent_id"], task.id, "main_pm", "x")
    # No main_pm in scope; the unknown-role branch would also short-circuit
    assert out is None


# ---------------------------------------------------------------------------
# unblock_with_restore - full snapshot path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unblock_with_restore_with_invalid_pre_block_state(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """An invalid pre_block_state value falls through to legacy unblock."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.BLOCKED
    task.pre_block_state = "garbage"
    # Has a branch (was claimed before blocking) so legacy unblock resumes
    # in_progress rather than returning a never-claimed task to pending.
    task.branch_name = "feature/backend/abc12345"
    await db_session.flush()
    out = await svc.unblock_with_restore(
        pm_agent_id=task_setup["agent_id"], task_id=task.id, restore=True
    )
    # Falls through to legacy unblock which transitions to in_progress
    assert out is not None
    assert out.status == TaskStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_unblock_with_restore_when_status_not_blocked(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.pre_block_state = TaskStatus.IN_PROGRESS.value
    await db_session.flush()
    out = await svc.unblock_with_restore(
        pm_agent_id=task_setup["agent_id"], task_id=task.id, restore=True
    )
    # status not BLOCKED but pre_block_state is set → returns None
    assert out is None


@pytest.mark.asyncio
async def test_soft_block_snapshots_pre_block_state(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """soft_block records the resting status + owner for restore=True."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    task.branch_name = "feature/backend/abc12345"
    await db_session.flush()
    await svc.soft_block(
        task.id, SoftBlockInfo(reason="x", blocker_type="ext", what_needed="y")
    )
    assert task.status == TaskStatus.BLOCKED
    assert task.pre_block_state == TaskStatus.IN_PROGRESS.value
    assert task.pre_block_assignee == task_setup["agent_id"]


@pytest.mark.asyncio
async def test_unblock_with_restore_returns_to_snapshot(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """restore=True returns the task to its snapshotted status + owner."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    task.branch_name = "feature/backend/abc12345"
    await db_session.flush()
    await svc.soft_block(
        task.id, SoftBlockInfo(reason="x", blocker_type="ext", what_needed="y")
    )
    out = await svc.unblock_with_restore(
        pm_agent_id=task_setup["agent_id"], task_id=task.id, restore=True
    )
    assert out is not None
    assert out.status == TaskStatus.IN_PROGRESS
    assert out.assigned_to == task_setup["agent_id"]
    assert out.claimed_by == task_setup["agent_id"]
    # Snapshot is consumed so a later block re-captures fresh.
    assert out.pre_block_state is None
    assert out.pre_block_assignee is None


@pytest.mark.asyncio
async def test_unblock_with_restore_branchless_diverts_to_pending(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """A snapshot of in_progress with no branch restores to pending, not in_progress.

    Restoring a branchless task to in_progress would loop the dispatcher; the
    restore path applies the same branchless guard legacy unblock() uses.
    """
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.BLOCKED
    task.pre_block_state = TaskStatus.IN_PROGRESS.value
    task.pre_block_assignee = task_setup["agent_id"]
    task.branch_name = None
    await db_session.flush()
    out = await svc.unblock_with_restore(
        pm_agent_id=task_setup["agent_id"], task_id=task.id, restore=True
    )
    assert out is not None
    assert out.status == TaskStatus.PENDING
    assert out.assigned_to == task_setup["agent_id"]


# ---------------------------------------------------------------------------
# qa_pass and qa_fail with actor mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_pass_logs_actor_mismatch(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    qa_a = AgentTable(
        id=uuid4(),
        name="QA-A",
        slug=f"be-qa-a-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    qa_b = AgentTable(
        id=uuid4(),
        name="QA-B",
        slug=f"be-qa-b-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add_all([qa_a, qa_b])
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.claimed_by = qa_a.id  # Different from qa_b
    task.pr_number = 1
    task.pr_url = "u"
    await db_session.flush()
    out = await svc.qa_pass(qa_b.id, task.id, "looks ok")
    # Pass succeeds despite mismatch (warning logged)
    assert out is not None


@pytest.mark.asyncio
async def test_qa_fail_appends_issues_and_calls_fail_qa(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    qa_agent = AgentTable(
        id=uuid4(),
        name="QA",
        slug=f"be-qa-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(qa_agent)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.claimed_by = qa_agent.id
    await db_session.flush()
    out = await svc.qa_fail(
        qa_agent.id,
        task.id,
        notes="needs work",
        issues=["typo", "missing test"],
    )
    assert out is not None
    assert out.status == TaskStatus.NEEDS_REVISION
    # Issues block was appended to dev_notes
    assert "typo" in (out.dev_notes or "")
    assert "missing test" in (out.dev_notes or "")


@pytest.mark.asyncio
async def test_qa_fail_returns_none_for_missing_task(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    out = await svc.qa_fail(uuid4(), uuid4(), notes="x", issues=[])
    assert out is None


# ---------------------------------------------------------------------------
# pause_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_for_agent_returns_none_when_wrong_assignee(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    other = AgentTable(
        id=uuid4(),
        name="Other",
        slug=f"other-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = other.id
    await db_session.flush()
    out = await svc.pause_for_agent(
        agent_id=task_setup["agent_id"], task_id=task.id, agent_role="developer"
    )
    assert out is None


@pytest.mark.asyncio
async def test_pause_for_agent_calls_pause_when_owner(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    out = await svc.pause_for_agent(
        agent_id=task_setup["agent_id"],
        task_id=task.id,
        agent_role="developer",
    )
    assert out is not None
    assert out.status == TaskStatus.PAUSED


# ---------------------------------------------------------------------------
# list_in_progress_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_in_progress_for_agent(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    rows = await svc.list_in_progress_for_agent(task_setup["agent_id"])
    assert task.id in {t.id for t in rows}


# ---------------------------------------------------------------------------
# create_subtask
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_subtask_requires_parent_task_id(task_setup: dict) -> None:
    svc = task_setup["svc"]
    with pytest.raises(ValueError, match="parent_task_id"):
        await svc.create_subtask(_req(task_setup, parent_task_id=None))


@pytest.mark.asyncio
async def test_create_subtask_with_assignee_uses_pending(
    task_setup: dict,
) -> None:
    # `create_subtask` enforces TASK_AT_CREATE completeness (Task 18, 2026-05-10),
    # so we pass a description that meets the 20-char minimum.
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    sub = await svc.create_subtask(
        _req(
            task_setup,
            description="Subtask with explicit description for completeness rule.",
            parent_task_id=parent.id,
            assigned_to=task_setup["agent_id"],
        )
    )
    assert sub.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_create_subtask_without_assignee_uses_backlog(
    task_setup: dict,
) -> None:
    # See test above re: completeness rule on description length.
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    sub = await svc.create_subtask(
        _req(
            task_setup,
            description="Subtask with explicit description for completeness rule.",
            parent_task_id=parent.id,
            assigned_to=None,
        )
    )
    assert sub.status == TaskStatus.BACKLOG


# ---------------------------------------------------------------------------
# submit_pm_review (gateway alias)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_pm_review_with_notes(
    task_setup: dict,
    db_session: AsyncSession,
) -> None:
    svc = task_setup["svc"]
    cell_pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(cell_pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.branch_name = "feature/backend/x"
    task.pr_created = True
    task.pr_number = 1
    await db_session.flush()
    out = await svc.submit_pm_review(cell_pm.id, task.id, "ready")
    assert out is not None


# ---------------------------------------------------------------------------
# resolve_agent_id failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_agent_id_raises_for_unknown_slug(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.resolve_agent_id("nonexistent-slug")


# ---------------------------------------------------------------------------
# main_pm_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_pm_agent_returns_seeded(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    main_pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="m",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(main_pm)
    await db_session.flush()
    out = await svc.main_pm_agent()
    assert out is not None
    assert out.role == AgentRole.MAIN_PM


# ---------------------------------------------------------------------------
# get_active_task_for_agent — returns the most recent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_task_for_agent_picks_in_progress(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    out = await svc.get_active_task_for_agent(task_setup["agent_id"])
    assert out is not None
    assert out.id == task.id


# ---------------------------------------------------------------------------
# list_paused_for_agent — returns paused tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_paused_for_agent_returns_paused_only(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    paused = await svc.create(_req(task_setup))
    paused.status = TaskStatus.PAUSED
    paused.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    rows = await svc.list_paused_for_agent(task_setup["agent_id"])
    assert paused.id in {t.id for t in rows}


# ---------------------------------------------------------------------------
# list_long_running_blocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_long_running_blocked_returns_list(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_long_running_blocked(threshold_minutes=1)
    assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# list_strategic_for_board
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_strategic_for_board_excludes_subtasks(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Only root tasks (no parent) with NON_TECHNICAL nature in PM review."""
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup, nature=TaskNature.NON_TECHNICAL))
    parent.status = TaskStatus.AWAITING_PM_REVIEW
    sub = await svc.create(
        _req(
            task_setup,
            nature=TaskNature.NON_TECHNICAL,
            parent_task_id=parent.id,
        )
    )
    sub.status = TaskStatus.AWAITING_PM_REVIEW
    await db_session.flush()
    rows = await svc.list_strategic_for_board()
    ids = {t.id for t in rows}
    assert parent.id in ids
    assert sub.id not in ids


# ---------------------------------------------------------------------------
# emit_task_event coverage — best-effort
# ---------------------------------------------------------------------------


class _BadBus:
    def is_connected(self) -> bool:
        raise RuntimeError("bus dead")


def _bad_bus_factory() -> _BadBus:
    return _BadBus()


@pytest.mark.asyncio
async def test_emit_task_event_swallows_exception(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If event bus raises, _emit_task_event must log + return without raising."""
    svc = task_setup["svc"]
    monkeypatch.setattr("roboco.services.task.get_event_bus", _bad_bus_factory)
    # No raise:
    await svc._emit_task_event(EventType.TASK_AWAITING_CEO_APPROVAL, uuid4())


@pytest.mark.asyncio
async def test_emit_task_event_publishes_when_connected(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    publish_mock = AsyncMock()

    class _Bus:
        def is_connected(self) -> bool:
            return True

        async def publish(self, event: Any) -> None:
            await publish_mock(event)

    def _bus_factory() -> _Bus:
        return _Bus()

    monkeypatch.setattr("roboco.services.task.get_event_bus", _bus_factory)
    await svc._emit_task_event(
        EventType.TASK_AWAITING_CEO_APPROVAL, uuid4(), {"key": "value"}
    )
    publish_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# resolve_pm_for_review chain walks up parents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_pm_for_review_finds_assignee_up_chain(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    pm_agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm_agent)
    await db_session.flush()
    grandparent = await svc.create(_req(task_setup, assigned_to=pm_agent.id))
    parent = await svc.create(_req(task_setup, parent_task_id=grandparent.id))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    pm_id = await svc._resolve_pm_for_review(child)
    assert pm_id == pm_agent.id


@pytest.mark.asyncio
async def test_resolve_pm_for_review_returns_none_when_root(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    root = await svc.create(_req(task_setup))
    pm_id = await svc._resolve_pm_for_review(root)
    assert pm_id is None


@pytest.mark.asyncio
async def test_resolve_pm_for_review_returns_none_when_parent_chain_unassigned(
    task_setup: dict,
) -> None:
    """Edge case: when no ancestor in the chain has an assignee."""
    svc = task_setup["svc"]
    grand = await svc.create(_req(task_setup))  # Unassigned
    parent = await svc.create(_req(task_setup, parent_task_id=grand.id))  # Unassigned
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    pm_id = await svc._resolve_pm_for_review(child)
    assert pm_id is None

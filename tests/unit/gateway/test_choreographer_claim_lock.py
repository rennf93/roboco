"""F074 — the one-task-per-agent invariant had no DB-level enforcement.

``_run_claim_guards`` read the agent's other tasks via unlocked SELECTs
(``list_in_progress_for_agent`` / ``list_paused_for_agent``) BEFORE ``claim()``
took its row lock, and ``claim()``'s ``FOR UPDATE`` locked only the TARGET row
— not the agent-wide invariant. So two concurrent ``i_will_work_on`` calls by
the SAME agent on TWO DIFFERENT pending tasks each locked their own target row
(no contention), each read an empty in_progress set, each passed
``already_active_guard``, and each claim+start succeeded → the agent ended with
two in_progress tasks. The in-process ``asyncio.Lock`` serializes container
spawns per agent but is lost on orchestrator-restart split-brain, so it is not
a DB-level guarantee.

The fix: a PostgreSQL transaction-scoped advisory lock keyed by agent_id,
acquired in ``_claim_plan_start_gate`` BEFORE the guard reads (for non-
coordinator roles only). Held until the request transaction commits, it spans
the guard read + the savepoint + the claim write, so the second concurrent
claim's guard read sees the first's committed in_progress task and is rejected.

CRITICAL logical-regression guard: the advisory lock is acquired ONLY for non-
coordinator roles. The PM coordinator concurrency feature (CLAUDE.md) lets a
cell_pm / main_pm plan + delegate many roots in parallel — acquiring a per-
agent advisory lock for a coordinator would serialize those claims and
REGRESS that feature. So coordinators are exempt (matching the existing
``_COORDINATOR_ROLES`` guard exemption for ``already_active`` / ``paused``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

# A developer fresh claim must carry a substantive step checklist + rich plan
# (parity with a PM) so the tracing-gate passes and the flow reaches claim.
_STEPS = [
    {
        "title": "Implement the change",
        "description": (
            "edit the target file, add tests, run them, and stage the "
            "change for commit on the task branch"
        ),
    }
]
_GOOD_PLAN = (
    "Append the timestamp HTML comment to the very bottom of README.md without "
    "touching any other line, then commit it on the task branch and open a PR. "
    "Verify the diff is a single-line addition before submitting for QA."
)
_GOOD_TC = ["Use a trailing newline so the comment sits on its own line."]
_GOOD_RISKS = [
    {
        "risk": "An accidental reformat of README.md balloons the diff.",
        "mitigation": "Append only; assert the diff touches one line pre-commit.",
    }
]


def _make_deps(task: AsyncMock) -> ChoreographerDeps:
    task.session = MagicMock()
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    evidence_repo = AsyncMock()
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(evidence_repo, method).return_value = []
    journal = AsyncMock()
    journal.latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(
        task=task,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=journal,
        audit=AsyncMock(),
        evidence_repo=evidence_repo,
    )


def _dev_task_svc(agent_id: object, task_id: object) -> AsyncMock:
    """TaskService mock that completes the dev claim→plan→start flow."""
    pending = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        quick_context=None,
    )
    in_progress = MagicMock(
        id=task_id, status="in_progress", plan={"text": "do x"}, assigned_to=agent_id
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = MagicMock(
        id=task_id, status="claimed", plan=None, assigned_to=agent_id
    )
    task_svc.set_plan.return_value = MagicMock(
        id=task_id, status="claimed", plan={"text": "do x"}, assigned_to=agent_id
    )
    task_svc.start.return_value = in_progress
    return task_svc


@pytest.mark.asyncio
async def test_dev_claim_acquires_lock_before_guard_read() -> None:
    """A developer claim acquires the per-agent advisory lock BEFORE the
    already_active guard reads the agent's other tasks — the ordering that
    closes the TOCTOU (the second concurrent claim's read must see the first's
    committed in_progress task, which requires the lock to be held first)."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _dev_task_svc(agent_id, task_id)

    # Shared call-order recorder: the lock MUST be acquired before the guard
    # read, so a shared list distinguishes the correct ordering from the bug
    # (read-then-claim, where the lock is too late).
    calls: list[str] = []

    async def _lock(_aid: object) -> None:
        calls.append("lock")

    async def _read_in_progress(_aid: object) -> list[Any]:
        calls.append("read_in_progress")
        return []

    async def _read_paused(_aid: object) -> list[Any]:
        calls.append("read_paused")
        return []

    task_svc.acquire_claim_lock = _lock
    task_svc.list_in_progress_for_agent.side_effect = _read_in_progress
    task_svc.list_paused_for_agent.side_effect = _read_paused

    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(
        agent_id,
        task_id,
        plan=_GOOD_PLAN,
        steps=_STEPS,
        technical_considerations=_GOOD_TC,
        risks=_GOOD_RISKS,
    )
    assert env.error is None, env.as_dict()

    # The lock was acquired once for this agent, and BEFORE the guard reads.
    assert calls[:1] == ["lock"], (
        f"advisory lock must be acquired before the guard reads; order was {calls}"
    )
    assert "read_in_progress" in calls and calls.index("lock") < calls.index(
        "read_in_progress"
    )


@pytest.mark.asyncio
async def test_coordinator_claim_does_not_acquire_lock() -> None:
    """A coordinator PM (cell_pm / main_pm) must NOT acquire the per-agent
    advisory lock — the PM coordinator concurrency feature (CLAUDE.md) lets a
    PM plan + delegate many roots in parallel. Acquiring the lock would
    serialize those claims and REGRESS that feature. The lock is only for the
    one-task-per-agent invariant, which coordinators are exempt from (matching
    the existing ``_COORDINATOR_ROLES`` already_active/paused guard exemption)."""
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    pending = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        task_type="planning",
        parent_task_id=None,
        sequence=0,
        team="backend",
        commits=[],
        pr_number=None,
        branch_name=None,
        quick_context=None,
        work_session_id=None,
    )
    task_svc.get.return_value = pending
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = MagicMock(
        id=task_id, status="claimed", plan=None, assigned_to=pm_id
    )
    task_svc.set_plan.return_value = MagicMock(
        id=task_id, status="claimed", plan={"text": "x"}, assigned_to=pm_id
    )
    task_svc.start.return_value = MagicMock(
        id=task_id, status="in_progress", plan={"text": "x"}, assigned_to=pm_id
    )
    task_svc.ensure_work_session.return_value = None

    lock_calls: list[object] = []

    async def _lock(aid: object) -> None:
        lock_calls.append(aid)

    task_svc.acquire_claim_lock = _lock

    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="decompose the feature into cell tasks with acceptance criteria",
        rich_plan={
            "approach": (
                "Two-slice decomposition: backend builds the API + DB migration "
                "first, UX consumes it after the endpoint merges. Strict "
                "sequencing; the UX slice depends on the backend slice landing."
            ),
            "sub_tasks": [
                {
                    "title": "Backend slice",
                    "description": (
                        "be-dev-1 implements the API endpoint and DB migration, "
                        "with tests, behind the existing service layer."
                    ),
                },
                {
                    "title": "UX slice",
                    "description": (
                        "fe-dev-1 wires the panel view to the new endpoint and "
                        "renders the result list with loading + error states."
                    ),
                },
            ],
            "risks": [
                {
                    "risk": "schema migration may block",
                    "mitigation": "rehearse the migration on a throwaway DB first",
                }
            ],
        },
    )
    body = env.as_dict()
    # The flow MUST reach the claim gate (not short-circuit at input validation)
    # — otherwise ``lock_calls == []`` would pass for the wrong reason.
    assert body.get("error") != "incomplete_input", body

    # The coordinator did NOT acquire the lock — parallel planning preserved.
    assert lock_calls == [], (
        "coordinator PM must not acquire the per-agent claim lock (would regress "
        "the PM coordinator concurrency feature)"
    )


# ---------------------------------------------------------------------------
# F124: the unmet_dependency guard reads dependency state via an unlocked
# SELECT, then fires release_dependency_blocked_claim (a state mutation:
# claimed/in_progress -> pending, clears branch_name, abandons WorkSession)
# as a side-effect BEFORE returning the rejection. If an upstream dependency
# completes (transitions to completed/cancelled) in the microseconds between
# the read and the release, the task is NEEDLESSLY released — its branch
# cleared + WorkSession abandoned + assignee bounced, only to be re-dispatched
# + re-claimed when the dependency-completion re-dispatch fires. Dependencies
# are monotonic (unmet -> met, terminal: completed/cancelled never reopen), so
# a fresh re-read that now finds them met stays met: safe to proceed without
# releasing. The fix re-checks unmet_dependency_ids immediately before the
# release and skips it (returning None — proceed) when the upstream just
# completed. The "still unmet" path is byte-for-byte the prior behavior.
# ---------------------------------------------------------------------------

# Initial dependency read + the re-check before release (F124).
_DEP_READ_INITIAL_PLUS_RECHECK = 2


def _dep_task_svc(agent_id: object, task_id: object, dep_id: object) -> AsyncMock:
    """TaskService mock for the dependency-guard race test. The task carries one
    dependency; the guard reads its state via ``unmet_dependency_ids``."""
    task = MagicMock(
        id=task_id,
        status="claimed",
        assigned_to=agent_id,
        parent_task_id=None,
        dependency_ids=[dep_id],
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    return task_svc


@pytest.mark.asyncio
async def test_dependency_guard_skips_release_when_upstream_just_completed() -> None:
    """F124: the first dependency read sees the upstream still unmet, but by the
    re-check (a few microseconds later) it has completed. The guard must NOT
    release the task — the dependency is now met, so the task can proceed.
    Releasing would needlessly clear its branch + abandon its WorkSession only
    to be re-dispatched + re-claimed when the dependency-completion re-dispatch
    fires. Returns None (proceed), no release."""
    agent_id = uuid4()
    task_id = uuid4()
    dep_id = uuid4()
    task_svc = _dep_task_svc(agent_id, task_id, dep_id)
    # First read: upstream still unmet. Re-check: upstream just completed -> met.
    task_svc.unmet_dependency_ids.side_effect = [[dep_id], []]

    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    guard = await c._run_claim_guards(
        agent_id=agent_id,
        task=task_svc.get.return_value,
        role_str="developer",
    )

    # The dependency just completed — the task can proceed, so the guard
    # returns None (no rejection) and does NOT release the task.
    assert guard is None, guard
    task_svc.release_dependency_blocked_claim.assert_not_awaited()
    # The re-check happened (initial read + re-check): the first saw unmet,
    # the second saw met.
    assert task_svc.unmet_dependency_ids.await_count == _DEP_READ_INITIAL_PLUS_RECHECK


@pytest.mark.asyncio
async def test_dependency_guard_releases_when_still_unmet_no_regression() -> None:
    """F124 no-regression: both the first read AND the re-check see the upstream
    still unmet. The guard releases the task to pending (stopping respawn churn
    into a blocked task) and returns the rejection — byte-for-byte the prior
    behavior. The re-check must not weaken the genuine-blocked release path."""
    agent_id = uuid4()
    task_id = uuid4()
    dep_id = uuid4()
    task_svc = _dep_task_svc(agent_id, task_id, dep_id)
    # Both reads: upstream still unmet.
    task_svc.unmet_dependency_ids.side_effect = [[dep_id], [dep_id]]

    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    guard = await c._run_claim_guards(
        agent_id=agent_id,
        task=task_svc.get.return_value,
        role_str="developer",
    )

    # Still unmet -> release + reject (the unchanged genuine-blocked path).
    assert guard is not None, guard
    assert guard.error == "invalid_state", guard.as_dict()
    task_svc.release_dependency_blocked_claim.assert_awaited_once_with(task_id)
    assert task_svc.unmet_dependency_ids.await_count == _DEP_READ_INITIAL_PLUS_RECHECK

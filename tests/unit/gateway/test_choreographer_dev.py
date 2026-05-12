"""Tests for the developer-facing Choreographer methods."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: AsyncMock) -> ChoreographerDeps:
    task = overrides.get("task", AsyncMock())
    # VerbRunner uses task.session.begin_nested() as a savepoint context
    # manager. AsyncMock auto-attributes any access (so hasattr always
    # returns True); we always overwrite session to a MagicMock with the
    # correct async-context-manager protocol.
    task.session = MagicMock()
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    work_session = overrides.get("work_session", AsyncMock())
    git = overrides.get("git", AsyncMock())
    a2a = overrides.get("a2a", AsyncMock())
    journal = overrides.get("journal", AsyncMock())
    audit = overrides.get("audit", AsyncMock())
    evidence_repo = overrides.get("evidence_repo", AsyncMock())
    # Ensure evidence_repo returns empty lists by default
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
    ):
        getattr(evidence_repo, method).return_value = []
    return ChoreographerDeps(
        task=task,
        work_session=work_session,
        git=git,
        a2a=a2a,
        journal=journal,
        audit=audit,
        evidence_repo=evidence_repo,
    )


@pytest.mark.asyncio
async def test_give_me_work_returns_assigned_task() -> None:
    agent_id = uuid4()
    task_obj = MagicMock(id=uuid4(), status="pending", title="t1")
    task_svc = AsyncMock()
    task_svc.list_pending_for_agent.return_value = []
    task_svc.list_assigned_for_agent.return_value = [task_obj]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["status"] == "pending"
    assert body["task_id"] == str(task_obj.id)
    assert "i_will_work_on" in body["next"]


@pytest.mark.asyncio
async def test_give_me_work_returns_paused_when_no_assigned() -> None:
    agent_id = uuid4()
    paused_obj = MagicMock(id=uuid4(), status="paused")
    task_svc = AsyncMock()
    task_svc.list_pending_for_agent.return_value = []
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = [paused_obj]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["task_id"] == str(paused_obj.id)
    assert "resume" in body["next"]


@pytest.mark.asyncio
async def test_give_me_work_returns_idle_when_no_work() -> None:
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_pending_for_agent.return_value = []
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["status"] == "idle"
    assert "i_am_idle" in body["next"]


@pytest.mark.asyncio
async def test_i_will_work_on_pending_with_plan() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    pending_task = MagicMock(
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
    in_progress_task = MagicMock(
        id=task_id, status="in_progress", plan={"text": "do x"}, assigned_to=agent_id
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending_task
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
    task_svc.start.return_value = in_progress_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id, plan="do x then y")
    assert env.error is None
    assert env.status == "in_progress"
    task_svc.claim.assert_awaited_once_with(task_id, agent_id)
    task_svc.set_plan.assert_awaited_once()
    task_svc.start.assert_awaited_once_with(task_id, agent_id)


@pytest.mark.asyncio
async def test_i_will_work_on_pending_no_plan_returns_tracing_gap() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    pending_task = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        description="task description",
        parent_task_id=None,
        sequence=0,
        task_type="code",
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        quick_context=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending_task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = MagicMock(
        id=task_id, status="claimed", plan=None, assigned_to=agent_id
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id, plan=None)
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "plan" in body["missing"]


@pytest.mark.asyncio
async def test_i_will_work_on_needs_revision_re_starts() -> None:
    """needs_revision dev path: spec composes (claim, set_plan, start), so
    claim now runs even when the task is already assigned to the dev (the
    spec source-status for claim includes NEEDS_REVISION). Migration
    behavior change vs. the pre-spec verb body, which skipped claim if
    already assigned."""
    agent_id = uuid4()
    task_id = uuid4()
    nr_task = MagicMock(
        id=task_id,
        status="needs_revision",
        assigned_to=agent_id,
        plan={"x": 1},
        task_type="code",
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        quick_context=None,
        parent_task_id=None,
        sequence=0,
        team="backend",
    )
    claimed = MagicMock(
        id=task_id, status="claimed", assigned_to=agent_id, plan={"x": 1}
    )
    in_progress_task = MagicMock(
        id=task_id, status="in_progress", assigned_to=agent_id, plan={"x": 1}
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = nr_task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = claimed
    task_svc.set_plan.return_value = claimed
    task_svc.start.return_value = in_progress_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id)
    assert env.status == "in_progress"
    task_svc.start.assert_awaited_once_with(task_id, agent_id)


@pytest.mark.asyncio
async def test_i_will_work_on_task_not_found_returns_not_found() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id)
    body = env.as_dict()
    assert body["error"] == "not_found"


@pytest.mark.asyncio
async def test_i_will_work_on_invalid_state_returns_invalid_state() -> None:
    """Completed task: spec rejects via can_invoke_action on the first
    composed action (claim) — completed is not in claim's source_statuses,
    so the message comes from the spec, not the verb body."""
    agent_id = uuid4()
    task_id = uuid4()
    completed_task = MagicMock(
        id=task_id,
        status="completed",
        assigned_to=agent_id,
        task_type="code",
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        quick_context=None,
        parent_task_id=None,
        sequence=0,
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = completed_task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    # Spec produces "task is in 'completed', 'claim' requires: ..."
    assert "completed" in body["message"]


@pytest.mark.asyncio
async def test_i_will_work_on_blocks_when_journal_note_at_claim_missing() -> None:
    """Pre-gateway parity P1: i_will_work_on requires a journal:note at claim.

    The composed (claim, set_plan, start) sequence runs first — the claim
    sticks. Then the post-claim tracing gate fires because no journal:note
    exists for (agent, task), and the agent gets a tracing_gap with a
    remediation hint to write a note and retry.
    """
    agent_id = uuid4()
    task_id = uuid4()
    pending_task = MagicMock(
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
    in_progress_task = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "do x"},
        assigned_to=agent_id,
    )
    task_svc = AsyncMock()
    # `get` is called twice: once at verb entry, once by _post_claim_journal_gate.
    task_svc.get.side_effect = [pending_task, in_progress_task]
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
    task_svc.start.return_value = in_progress_task
    journal_svc = AsyncMock()
    journal_svc.has_note_for_task.return_value = False
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id, plan="do x then y")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:note_at_claim" in body["missing"]
    assert "note(scope='note'" in body["remediate"]
    # The composed action ran — claim+set_plan+start were called even though
    # the post-claim gate failed.
    task_svc.claim.assert_awaited_once_with(task_id, agent_id)
    task_svc.start.assert_awaited_once_with(task_id, agent_id)


# test_i_am_done_with_catchup_full_chain removed (audit P2-5/D-16):
# i_am_done_with_catchup verb deleted. submit_for_qa now does push + PR
# explicitly; i_am_done auto-runs submit_verification + submit_qa.


@pytest.mark.asyncio
async def test_i_am_done_blocks_when_acceptance_criteria_unaddressed() -> None:
    """Without a reflect note, unaddressed criteria block i_am_done.

    The reflect note is treated as the addressing artifact for any
    criterion not explicitly cited via acceptance_criteria_status —
    so this test deliberately omits reflect to surface the criterion
    rejection.
    """
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        plan={"x": 1},
        branch_name="feature/backend/abc",
        work_session_id=uuid4(),
        self_verified=False,
        progress_updates=[{"message": "p"}],
        acceptance_criteria=["AC1", "AC2"],
        acceptance_criteria_status=[
            {"criterion": "AC1", "referencing_artifact_id": "c1"}
        ],
        # Spec's PRECONDITION_COMMITS now runs before the tracing gate;
        # supply a commit so the tracing gap (acceptance criteria) is
        # the load-bearing rejection.
        commits=[{"sha": "abc"}],
        pr_number=8,
        pr_url="https://x/pr/8",
        team="backend",
        documents=[],
        dev_notes="",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = False
    # JOURNAL_DURING_WORK_AT_LEAST_ONE is satisfied so the load-bearing
    # rejection here is the unaddressed AC2 criterion, not the new
    # mid-flight cadence gate.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert any("AC2" in m for m in body["missing"])


@pytest.mark.asyncio
async def test_i_am_done_reflect_note_addresses_acceptance_criteria() -> None:
    """A reflect note clears the acceptance-criteria gate.

    Per `_check_acceptance_criteria`, the reflect note is the agent's
    attestation that the work meets every criterion; once it's present,
    unaddressed criteria no longer block the submission. The other
    tracing requirements (commits, PR, progress) still apply.
    """
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        plan={"x": 1},
        branch_name="feature/backend/abc",
        work_session_id=uuid4(),
        self_verified=False,
        progress_updates=[{"message": "p"}],
        acceptance_criteria=["AC1", "AC2"],
        acceptance_criteria_status=[],  # nothing cited explicitly
        commits=[{"sha": "abc"}],
        pr_number=8,
        pr_url="https://x/pr/8",
        team="backend",
        documents=[],
        dev_notes="",
        qa_notes="",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.submit_verification.return_value = t
    task_svc.submit_for_qa.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    # Satisfy JOURNAL_DURING_WORK_AT_LEAST_ONE so the test's narrow assertion
    # (criteria-gap cleared) isn't masked by an unrelated tracing failure.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    # criteria gate is cleared by reflect; if anything else fails, it
    # must NOT be the AC2 criterion.
    if body.get("error") == "tracing_gap":
        assert not any("AC2" in m for m in body.get("missing", []))


@pytest.mark.asyncio
async def test_i_am_done_blocks_when_journal_reflect_missing() -> None:
    """Tracing-gate (journal:reflect) fires after the spec gate accepts."""
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        plan={"x": 1},
        branch_name="feature/backend/abc",
        work_session_id=uuid4(),
        self_verified=False,
        progress_updates=[{"message": "p"}],
        acceptance_criteria=["AC1"],
        acceptance_criteria_status=[
            {"criterion": "AC1", "referencing_artifact_id": "c1"}
        ],
        # Spec's PRECONDITION_COMMITS runs before the tracing gate; supply
        # a commit so the missing journal:reflect is the load-bearing gap.
        commits=[{"sha": "abc"}],
        pr_number=8,
        pr_url="https://x/pr/8",
        team="backend",
        documents=[],
        dev_notes="",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = False  # no reflect
    # Satisfy JOURNAL_DURING_WORK_AT_LEAST_ONE so journal:reflect is the
    # load-bearing gap surfaced to the assertion.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:reflect" in body["missing"]


@pytest.mark.asyncio
async def test_i_am_done_not_assigned_returns_tracing_gap() -> None:
    """Spec's PRECONDITION_OWNERSHIP rejects with tracing_gap (owns_task).

    Pre-spec migration the verb returned not_authorized via an inline
    ownership check; that's now driven by the spec's extra precondition
    so the rejection_kind is tracing_gap.
    """
    agent_id = uuid4()
    other_agent = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=other_agent,
        commits=[{"sha": "abc"}],
        team="backend",
        quick_context=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "x")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "owns_task" in body["missing"]


@pytest.mark.asyncio
async def test_i_am_done_skill_resolution_picks_existing_skill() -> None:
    """Resolver picks first matching skill.

    Falls back to the first preference entry when no match is found.
    """
    deps = _make_deps()
    c = Choreographer(deps)

    qa = MagicMock(id=uuid4(), skills=[{"id": "qa_review"}, {"id": "test_validation"}])
    skill = c._resolve_skill(qa, ["code_review", "qa_review"])
    assert skill == "qa_review"

    qa_with_canonical = MagicMock(id=uuid4(), skills=[{"id": "code_review"}])
    skill2 = c._resolve_skill(qa_with_canonical, ["code_review", "qa_review"])
    assert skill2 == "code_review"

    qa_with_neither = MagicMock(id=uuid4(), skills=[{"id": "other_skill"}])
    skill3 = c._resolve_skill(qa_with_neither, ["code_review", "qa_review"])
    assert skill3 == "code_review"  # fallback to first


@pytest.mark.asyncio
async def test_i_am_blocked_escalates_and_journals() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        pre_block_state=None,
        task_type="code",
        team="backend",
    )
    after = MagicMock(id=task_id, status="blocked", assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.escalate.return_value = after
    journal_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_blocked(agent_id, task_id, "external API down")
    assert env.error is None
    assert env.status == "blocked"
    journal_svc.write_struggle.assert_awaited_once()
    task_svc.escalate.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_am_blocked_task_not_found_returns_not_found() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_blocked(agent_id, task_id, "x")
    body = env.as_dict()
    assert body["error"] == "not_found"


@pytest.mark.asyncio
async def test_i_am_idle_with_unread_a2a_soft_blocks() -> None:
    agent_id = uuid4()
    deps = _make_deps()
    deps.evidence_repo.list_unread_a2a.return_value = [{"from": "x", "task_id": "t1"}]
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    body = env.as_dict()
    assert body["status"] == "idle_with_unread"
    assert "address" in body["next"].lower()


@pytest.mark.asyncio
async def test_i_am_idle_with_unread_mentions_soft_blocks() -> None:
    agent_id = uuid4()
    deps = _make_deps()
    deps.evidence_repo.list_unread_mentions.return_value = [
        {"channel": "#x", "from": "y"}
    ]
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    body = env.as_dict()
    assert body["status"] == "idle_with_unread"


@pytest.mark.asyncio
async def test_i_am_idle_clean_returns_idle() -> None:
    agent_id = uuid4()
    task_svc = AsyncMock()
    deps = _make_deps(task=task_svc)
    # All evidence_repo lists are already empty per _make_deps default
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    assert env.status == "idle"
    task_svc.mark_agent_idle.assert_awaited_once_with(agent_id)

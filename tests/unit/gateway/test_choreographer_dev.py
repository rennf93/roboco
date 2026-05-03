"""Tests for the developer-facing Choreographer methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: AsyncMock) -> ChoreographerDeps:
    task = overrides.get("task", AsyncMock())
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
    )
    in_progress_task = MagicMock(
        id=task_id, status="in_progress", plan={"text": "do x"}, assigned_to=agent_id
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending_task
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
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
    task_svc.claim.assert_awaited_once_with(agent_id, task_id)
    task_svc.set_plan.assert_awaited_once()
    task_svc.start.assert_awaited_once_with(agent_id, task_id)


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
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = pending_task
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
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
    assert "i_will_work_on" in body["remediate"]


@pytest.mark.asyncio
async def test_i_will_work_on_needs_revision_re_starts() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    nr_task = MagicMock(
        id=task_id, status="needs_revision", assigned_to=agent_id, plan={"x": 1}
    )
    in_progress_task = MagicMock(
        id=task_id, status="in_progress", assigned_to=agent_id, plan={"x": 1}
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = nr_task
    task_svc.start.return_value = in_progress_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id)
    assert env.status == "in_progress"
    task_svc.claim.assert_not_awaited()  # already assigned
    task_svc.start.assert_awaited_once_with(agent_id, task_id)


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
    agent_id = uuid4()
    task_id = uuid4()
    completed_task = MagicMock(id=task_id, status="completed", assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = completed_task
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "completed" in body["message"]


@pytest.mark.asyncio
async def test_i_have_committed_records_progress() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    active = MagicMock(
        id=task_id, status="in_progress", assigned_to=agent_id, plan={"x": 1}
    )
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = active
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_have_committed(agent_id, "feat(api): add /healthz endpoint")
    assert env.error is None
    task_svc.add_progress.assert_awaited_once_with(
        task_id, agent_id, "feat(api): add /healthz endpoint"
    )


@pytest.mark.asyncio
async def test_i_have_committed_no_active_task_returns_invalid_state() -> None:
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_have_committed(agent_id, "feat: x")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "give_me_work" in body["remediate"]


@pytest.mark.asyncio
async def test_i_have_committed_no_plan_returns_tracing_gap() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    active = MagicMock(
        id=task_id, status="in_progress", assigned_to=agent_id, plan=None
    )
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = active
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_have_committed(agent_id, "feat: x")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "plan" in body["missing"]
    task_svc.add_progress.assert_not_awaited()


@pytest.mark.asyncio
async def test_i_am_done_with_catchup_full_chain() -> None:
    """The catch-up convenience verb auto-runs verify/push/PR/submit_qa.

    Strict ``i_am_done`` requires the dev to have done these steps already
    (Gate Set E). When the dev wants the gateway to drive the chain, they
    call the explicit catch-up verb.
    """
    agent_id = uuid4()
    task_id = uuid4()
    branch = "feature/backend/abc--def"
    ws_id = uuid4()
    initial = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        plan={"x": 1},
        branch_name=branch,
        work_session_id=ws_id,
        self_verified=False,
        pr_number=None,
        pr_url=None,
        team="backend",
        progress_updates=[{"message": "did x"}],
        acceptance_criteria=["AC1"],
        acceptance_criteria_status=[
            {"criterion": "AC1", "referencing_artifact_id": "c1"}
        ],
        commits=[],
        documents=[],
        dev_notes="",
    )
    after_verify = MagicMock(
        id=task_id,
        status="verifying",
        assigned_to=agent_id,
        plan={"x": 1},
        branch_name=branch,
        work_session_id=ws_id,
        self_verified=True,
        pr_number=None,
        pr_url=None,
        team="backend",
        progress_updates=[{"message": "did x"}],
        acceptance_criteria=["AC1"],
        acceptance_criteria_status=[
            {"criterion": "AC1", "referencing_artifact_id": "c1"}
        ],
        commits=[],
        documents=[],
        dev_notes="",
    )
    after_pr = MagicMock(
        id=task_id,
        status="verifying",
        assigned_to=agent_id,
        plan={"x": 1},
        branch_name=branch,
        work_session_id=ws_id,
        self_verified=True,
        pr_number=8,
        pr_url="https://x/pr/8",
        team="backend",
        progress_updates=[{"message": "did x"}],
        acceptance_criteria=["AC1"],
        acceptance_criteria_status=[
            {"criterion": "AC1", "referencing_artifact_id": "c1"}
        ],
        commits=[],
        documents=[],
        dev_notes="",
    )
    after_submit = MagicMock(
        id=task_id,
        status="awaiting_qa",
        assigned_to=agent_id,
        plan={"x": 1},
        branch_name=branch,
        work_session_id=ws_id,
        self_verified=True,
        pr_number=8,
        pr_url="https://x/pr/8",
        team="backend",
        progress_updates=[{"message": "did x"}],
        acceptance_criteria=["AC1"],
        acceptance_criteria_status=[
            {"criterion": "AC1", "referencing_artifact_id": "c1"}
        ],
        commits=[],
        documents=[],
        dev_notes="",
    )

    task_svc = AsyncMock()
    task_svc.get.side_effect = [initial, after_pr]  # initial fetch + post-PR refresh
    task_svc.submit_verification.return_value = after_verify
    task_svc.submit_qa.return_value = after_submit
    task_svc.qa_agent_for_team.return_value = MagicMock(
        id=uuid4(), skills=[{"id": "code_review"}]
    )

    work_svc = AsyncMock()
    work_svc.has_unpushed_commits.return_value = True
    work_svc.files_changed.return_value = ["README.md"]

    git_svc = AsyncMock()
    git_svc.create_pr.return_value = {"pr_number": 8, "pr_url": "https://x/pr/8"}

    a2a_svc = AsyncMock()

    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True

    deps = _make_deps(
        task=task_svc,
        work_session=work_svc,
        git=git_svc,
        a2a=a2a_svc,
        journal=journal_svc,
    )
    deps.evidence_repo.journal_highlights_for_task.return_value = []
    c = Choreographer(deps)

    env = await c.i_am_done_with_catchup(agent_id, task_id, "all done")
    assert env.error is None
    assert env.status == "awaiting_qa"
    git_svc.push_branch.assert_awaited_once_with(branch)
    git_svc.create_pr.assert_awaited_once()
    a2a_svc.send.assert_awaited_once()
    body = env.as_dict()
    assert body["evidence"]["pr_url"] == "https://x/pr/8"


@pytest.mark.asyncio
async def test_i_am_done_blocks_when_acceptance_criteria_unaddressed() -> None:
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
        commits=[],
        documents=[],
        dev_notes="",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert any("AC2" in m for m in body["missing"])


@pytest.mark.asyncio
async def test_i_am_done_blocks_when_journal_reflect_missing() -> None:
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
        commits=[],
        documents=[],
        dev_notes="",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = False  # no reflect
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:reflect" in body["missing"]


@pytest.mark.asyncio
async def test_i_am_done_not_assigned_returns_not_authorized() -> None:
    agent_id = uuid4()
    other_agent = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="in_progress", assigned_to=other_agent)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "x")
    body = env.as_dict()
    assert body["error"] == "not_authorized"


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
        id=task_id, status="in_progress", assigned_to=agent_id, pre_block_state=None
    )
    after = MagicMock(id=task_id, status="blocked")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
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

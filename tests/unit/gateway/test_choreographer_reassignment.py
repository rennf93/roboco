"""Tests covering ``task.assigned_to`` reassignment on each Choreographer
lifecycle transition.

The orchestrator polls per-agent for actionable tasks. If ``assigned_to``
is not updated when the lifecycle moves to a new stage (qa, doc, pm,
ceo), the orchestrator keeps respawning the previous-stage agent — an
infinite retry loop the role permission gates correctly reject. The
choreographer must hand the task off to the right agent for the new
stage as part of every transition.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    # VerbRunner uses task.session.begin_nested() as a savepoint context
    # manager for spec-driven verbs (i_am_done, i_will_work_on, etc.).
    # Other choreographer codepaths (doc.py:i_documented) await
    # task.session.flush(), so keep the session itself an AsyncMock and
    # only override begin_nested with a sync MagicMock that returns the
    # async-context-manager protocol the runner expects.
    task = base["task"]
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    repo = base["evidence_repo"]
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


# ---------------------------------------------------------------------------
# i_am_done: dev → qa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_reassigns_task_to_qa_agent() -> None:
    """After dev submits, ``assigned_to`` must point at the team's QA."""
    dev_id = uuid4()
    task_id = uuid4()
    qa_id = uuid4()
    branch = "feature/backend/abc--def"
    ws_id = uuid4()
    initial = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=dev_id,
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
        # Gate Set E requires non-empty commits before submit_qa.
        commits=[{"sha": "abc"}],
        documents=[],
        dev_notes="",
    )
    after_verify = MagicMock(
        **{**initial.__dict__, "status": "verifying", "self_verified": True},
    )
    after_submit = MagicMock(
        **{**initial.__dict__, "status": "awaiting_qa", "assigned_to": None},
    )
    qa_agent = MagicMock(id=qa_id, skills=[{"id": "code_review"}])

    task_svc = AsyncMock()
    task_svc.get.return_value = initial
    task_svc.agent_for.return_value = MagicMock(
        id=dev_id, role="developer", team="backend", slug=None
    )
    task_svc.submit_verification.return_value = after_verify
    task_svc.submit_qa.return_value = after_submit
    task_svc.qa_agent_for_team.return_value = qa_agent

    work_svc = AsyncMock()
    work_svc.has_unpushed_commits.return_value = False
    work_svc.files_changed.return_value = []

    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    # JOURNAL_DURING_WORK_AT_LEAST_ONE: at least one decision/learning/struggle
    # must exist between claim and submit.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False

    deps = _make_deps(task=task_svc, work_session=work_svc, journal=journal_svc)
    deps.evidence_repo.journal_highlights_for_task.return_value = []
    c = Choreographer(deps)

    env = await c.i_am_done(dev_id, task_id, "all done")
    assert env.error is None
    task_svc.reassign.assert_awaited_once_with(task_id, qa_id)


@pytest.mark.asyncio
async def test_i_am_done_skips_reassign_when_no_qa_agent() -> None:
    """No QA configured for the team -> no reassign call (and no spawn)."""
    dev_id = uuid4()
    task_id = uuid4()
    branch = "feature/backend/abc"
    ws_id = uuid4()
    initial = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=dev_id,
        plan={"x": 1},
        branch_name=branch,
        work_session_id=ws_id,
        self_verified=True,
        pr_number=8,
        pr_url="https://x/pr/8",
        team="backend",
        progress_updates=[{"message": "p"}],
        acceptance_criteria=[],
        acceptance_criteria_status=[],
        # Gate Set E requires non-empty commits before submit_qa.
        commits=[{"sha": "abc"}],
        documents=[],
        dev_notes="",
    )
    after_verify = MagicMock(
        **{**initial.__dict__, "status": "verifying", "self_verified": True},
    )
    after_submit = MagicMock(
        **{**initial.__dict__, "status": "awaiting_qa", "assigned_to": None},
    )

    task_svc = AsyncMock()
    task_svc.get.return_value = initial
    task_svc.agent_for.return_value = MagicMock(
        id=dev_id, role="developer", team="backend", slug=None
    )
    task_svc.submit_verification.return_value = after_verify
    task_svc.submit_qa.return_value = after_submit
    task_svc.qa_agent_for_team.return_value = None  # no QA found

    work_svc = AsyncMock()
    work_svc.has_unpushed_commits.return_value = False
    work_svc.files_changed.return_value = []

    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    # JOURNAL_DURING_WORK_AT_LEAST_ONE.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False

    deps = _make_deps(task=task_svc, work_session=work_svc, journal=journal_svc)
    deps.evidence_repo.journal_highlights_for_task.return_value = []
    c = Choreographer(deps)

    await c.i_am_done(dev_id, task_id, "done")
    task_svc.reassign.assert_not_awaited()


# ---------------------------------------------------------------------------
# pass_review: qa → documenter
# ---------------------------------------------------------------------------


def _qa_awaiting_task(task_id: Any, qa_id: Any) -> MagicMock:
    return MagicMock(
        id=task_id,
        status="awaiting_qa",
        task_type="code",
        team="backend",
        assigned_to=qa_id,
        qa_evidence_inspected=True,
        quick_context=None,
    )


def _qa_agent(qa_id: Any) -> MagicMock:
    return MagicMock(id=qa_id, role="qa", team="backend", slug=None)


def _begin_nested_mock() -> Any:
    return MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )


@pytest.mark.asyncio
async def test_pass_review_reassigns_task_to_documenter() -> None:
    qa_id = uuid4()
    doc_id = uuid4()
    task_id = uuid4()
    t = _qa_awaiting_task(task_id, qa_id)
    after = MagicMock(
        id=task_id,
        status="awaiting_documentation",
        team="backend",
        pr_url="https://x/pr/8",
        assigned_to=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _qa_agent(qa_id)
    task_svc.qa_pass.return_value = after
    task_svc.documenter_for_team.return_value = MagicMock(id=doc_id)
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = _begin_nested_mock()
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    notes = (
        "Reviewed PR carefully. Branch convention correct. Commit prefix "
        "verified. README diff matches spec. All acceptance criteria met."
    )
    env = await c.pass_review(qa_id, task_id, notes=notes)
    assert env.error is None
    task_svc.reassign.assert_awaited_once_with(task_id, doc_id)


@pytest.mark.asyncio
async def test_pass_review_skips_reassign_when_no_documenter() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t = _qa_awaiting_task(task_id, qa_id)
    after = MagicMock(
        id=task_id,
        status="awaiting_documentation",
        team="backend",
        pr_url="x",
        assigned_to=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _qa_agent(qa_id)
    task_svc.qa_pass.return_value = after
    task_svc.documenter_for_team.return_value = None
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = _begin_nested_mock()
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    notes = "x" * 100
    await c.pass_review(qa_id, task_id, notes=notes)
    task_svc.reassign.assert_not_awaited()


# ---------------------------------------------------------------------------
# i_documented: documenter → cell_pm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_documented_reassigns_task_to_cell_pm() -> None:
    doc_id = uuid4()
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_documentation",
        task_type="code",
        assigned_to=doc_id,
        team="backend",
        quick_context=None,
        documents=[],
    )
    after = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=doc_id,
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=doc_id, role="documenter", team="backend", slug=None
    )
    task_svc.docs_complete.return_value = after
    task_svc.cell_pm_for_team.return_value = MagicMock(id=pm_id)
    task_svc.session = MagicMock()
    task_svc.session.flush = AsyncMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    notes = "Wrote backend/guides/feature-x.md with usage examples and config notes."
    files = ["backend/guides/feature-x.md"]
    env = await c.i_documented(doc_id, task_id, notes=notes, files=files)
    assert env.error is None
    task_svc.reassign.assert_awaited_once_with(task_id, pm_id)


# ---------------------------------------------------------------------------
# main_pm_complete: PM → CEO (assigned_to cleared so no agent respawns)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_pm_complete_clears_assignment_for_ceo() -> None:
    main_pm_id = uuid4()
    root_task_id = uuid4()
    t = MagicMock(
        id=root_task_id,
        status="awaiting_pm_review",
        assigned_to=main_pm_id,
        pr_number=42,
        branch_name="feature/backend/root123",
        parent_task_id=None,
        team="backend",
    )
    after = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.escalate_to_ceo.return_value = after
    task_svc.all_subtasks_terminal.return_value = True
    git_svc = AsyncMock()
    git_svc.pr_target.return_value = "master"
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.main_pm_complete(
        main_pm_id, root_task_id, notes="root scope reviewed and ready"
    )
    assert env.error is None
    task_svc.reassign.assert_awaited_once_with(root_task_id, None)


# ---------------------------------------------------------------------------
# escalate_to_ceo (board): clears assignment so CEO acts via UI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_escalate_to_ceo_clears_assignment() -> None:
    board_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        parent_task_id=None,
    )
    after = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="product_owner")
    task_svc.escalate_to_ceo.return_value = after
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.escalate_to_ceo(board_id, task_id, reason="cross-cell rollout")
    assert env.error is None
    task_svc.reassign.assert_awaited_once_with(task_id, None)


# ---------------------------------------------------------------------------
# cell_pm_complete: leaf done → walk up + reassign parent if all subtasks done
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cell_pm_complete_reassigns_parent_when_all_subtasks_done() -> None:
    pm_id = uuid4()
    leaf_id = uuid4()
    parent_id = uuid4()
    new_pm_id = uuid4()
    leaf = MagicMock(
        id=leaf_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        pr_number=8,
        branch_name="feature/backend/abc--def",
        parent_task_id=parent_id,
        team="backend",
    )
    after = MagicMock(
        **{**leaf.__dict__, "status": "completed"},
    )
    parent = MagicMock(
        id=parent_id,
        team="backend",
        parent_task_id=None,
    )

    task_svc = AsyncMock()
    # First .get is for the leaf (status check), then for the parent walk-up.
    task_svc.get.side_effect = [leaf, parent]
    task_svc.all_subtasks_terminal.side_effect = [True, True]
    task_svc.cell_pm_complete.return_value = after
    task_svc.cell_pm_for_team.return_value = MagicMock(id=new_pm_id)

    git_svc = AsyncMock()
    git_svc.pr_merge.return_value = {"merged": True, "merge_commit_sha": "abc"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.cell_pm_complete(
        pm_id, leaf_id, notes="cell scope reviewed and merged into parent"
    )
    assert env.error is None
    # Parent reassignment should have been issued, and only for the parent.
    task_svc.reassign.assert_awaited_once_with(parent_id, new_pm_id)


@pytest.mark.asyncio
async def test_cell_pm_complete_skips_parent_reassign_when_subtasks_pending() -> None:
    pm_id = uuid4()
    leaf_id = uuid4()
    parent_id = uuid4()
    leaf = MagicMock(
        id=leaf_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        pr_number=8,
        branch_name="feature/backend/abc--def",
        parent_task_id=parent_id,
        team="backend",
    )
    after = MagicMock(**{**leaf.__dict__, "status": "completed"})
    parent = MagicMock(id=parent_id, team="backend")
    task_svc = AsyncMock()
    # The leaf's own all_subtasks_terminal in the guard returns True (no
    # children); after completion, the parent walk-up's call returns False
    # because some sibling is still active.
    task_svc.get.side_effect = [leaf, parent]
    task_svc.all_subtasks_terminal.side_effect = [True, False]
    task_svc.cell_pm_complete.return_value = after
    git_svc = AsyncMock()
    git_svc.pr_merge.return_value = {"merged": True, "merge_commit_sha": "abc"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    await c.cell_pm_complete(pm_id, leaf_id, notes="reviewed")
    task_svc.reassign.assert_not_awaited()


@pytest.mark.asyncio
async def test_cell_pm_complete_skips_parent_walk_up_for_root_task() -> None:
    """Root tasks have parent_task_id=None — no walk-up should occur."""
    pm_id = uuid4()
    root_id = uuid4()
    leaf = MagicMock(
        id=root_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        pr_number=8,
        branch_name="feature/backend/root123",
        parent_task_id=None,
        team="backend",
    )
    after = MagicMock(**{**leaf.__dict__, "status": "completed"})
    task_svc = AsyncMock()
    task_svc.get.return_value = leaf
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.cell_pm_complete.return_value = after
    git_svc = AsyncMock()
    git_svc.pr_merge.return_value = {"merged": True, "merge_commit_sha": "abc"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    await c.cell_pm_complete(pm_id, root_id, notes="reviewed")
    task_svc.reassign.assert_not_awaited()


# ---------------------------------------------------------------------------
# fail_review: existing path leans on qa_fail's quick_context recovery; the
# choreographer should NOT issue an explicit reassign of its own (qa_fail
# already moves the task back to the original developer).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_review_does_not_double_reassign() -> None:
    qa_id = uuid4()
    dev_id = uuid4()
    task_id = uuid4()
    t = _qa_awaiting_task(task_id, qa_id)
    after = MagicMock(
        id=task_id,
        status="needs_revision",
        assigned_to=dev_id,
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _qa_agent(qa_id)
    task_svc.qa_fail.return_value = after
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = _begin_nested_mock()
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    a2a_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc, a2a=a2a_svc)
    c = Choreographer(deps)

    issues = [
        "Missing unit test coverage for /healthz endpoint",
        "Lint errors in /api/foo.py: unused import",
    ]
    env = await c.fail_review(qa_id, task_id, issues)
    assert env.error is None
    # qa_fail itself reassigns back to the original developer via quick_context;
    # the choreographer should not stack a second reassign on top.
    task_svc.reassign.assert_not_awaited()

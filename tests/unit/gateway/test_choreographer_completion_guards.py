"""Gate Set F: completion-time guards.

cell_pm_complete / main_pm_complete / submit_up must refuse to advance
the parent past awaiting_pm_review when any subtask is still non-
terminal. Pre-gateway location: roboco/services/task.py closure check.

These tests verify:
1. The non-terminal-subtask refusal fires.
2. The remediation NAMES the non-terminal subtasks (improvement over
   the previous generic "find pending subtasks" hint).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    # VerbRunner wraps composed atomic actions in
    # ``task.session.begin_nested()``. AsyncMock auto-attribute access
    # would return an unawaitable coroutine, breaking the
    # ``async with`` protocol. Overwrite session with a MagicMock that
    # implements the async-context-manager protocol explicitly.
    task_dep = base["task"]
    task_dep.session = MagicMock()
    task_dep.session.begin_nested = MagicMock(
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
# cell_pm_complete subtask gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cell_pm_complete_blocks_when_subtask_pending() -> None:
    pm_id = uuid4()
    parent_id = uuid4()
    sub_id = uuid4()
    t = MagicMock(
        id=parent_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        pr_number=10,
        team="backend",
        branch_name="feature/backend/abc",
    )
    sub = MagicMock(id=sub_id, status="pending", title="Half-done subtask")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = False
    task_svc.get_subtasks.return_value = [sub]
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.cell_pm_complete(
        pm_id, parent_id, "reviewed cell scope and merge ready"
    )
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    # Improvement: non-terminal subtask must be named.
    assert str(sub_id) in body["remediate"]
    task_svc.cell_pm_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_cell_pm_complete_allows_when_all_terminal() -> None:
    pm_id = uuid4()
    parent_id = uuid4()
    t = MagicMock(
        id=parent_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        pr_number=10,
        team="backend",
        branch_name="feature/backend/abc",
        parent_task_id=None,
    )
    after = MagicMock(**{**t.__dict__, "status": "completed"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.get_subtasks.return_value = []
    task_svc.cell_pm_complete.return_value = after
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    git_svc = AsyncMock()
    git_svc.pr_merge.return_value = {"merge_commit_sha": "abc"}
    deps = _make_deps(task=task_svc, journal=journal_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.cell_pm_complete(pm_id, parent_id, "cell scope reviewed and approved")
    assert env.error is None
    task_svc.cell_pm_complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_cell_pm_complete_allows_decision_without_separate_reflect() -> None:
    """A PM with a fresh decision but NO separate reflect can still complete —
    the decision documents the close, so the reflect gate no longer loops
    weak-model PMs into respawn churn."""
    pm_id = uuid4()
    parent_id = uuid4()
    t = MagicMock(
        id=parent_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        pr_number=10,
        team="backend",
        branch_name="feature/backend/abc",
        parent_task_id=None,
    )
    after = MagicMock(**{**t.__dict__, "status": "completed"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.get_subtasks.return_value = []
    task_svc.cell_pm_complete.return_value = after
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = False  # no separate reflect
    git_svc = AsyncMock()
    git_svc.pr_merge.return_value = {"merge_commit_sha": "abc"}
    deps = _make_deps(task=task_svc, journal=journal_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.cell_pm_complete(pm_id, parent_id, "cell scope reviewed and approved")
    assert env.error is None
    task_svc.cell_pm_complete.assert_awaited_once()


# ---------------------------------------------------------------------------
# main_pm_complete subtask gate (root-task case)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_pm_complete_blocks_when_subtask_pending() -> None:
    pm_id = uuid4()
    root_id = uuid4()
    sub_id = uuid4()
    t = MagicMock(
        id=root_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        parent_task_id=None,
        pr_number=10,
        team="backend",
        branch_name="feature/backend/abc",
    )
    sub = MagicMock(id=sub_id, status="in_progress", title="Subtask still active")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = False
    task_svc.get_subtasks.return_value = [sub]
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.main_pm_complete(
        pm_id, root_id, "root scope ready to ship to production"
    )
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert str(sub_id) in body["remediate"]
    task_svc.escalate_to_ceo.assert_not_awaited()


# ---------------------------------------------------------------------------
# submit_up subtask gate (cell PM bubbling up to main PM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_up_blocks_when_subtask_pending() -> None:
    pm_id = uuid4()
    parent_id = uuid4()
    sub_id = uuid4()
    t = MagicMock(
        id=parent_id,
        status="in_progress",
        assigned_to=pm_id,
        branch_name="feature/backend/abc",
        team="backend",
    )
    sub = MagicMock(id=sub_id, status="paused", title="Paused subtask")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.all_subtasks_terminal.return_value = False
    task_svc.get_subtasks.return_value = [sub]
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.submit_up(
        pm_id,
        parent_id,
        "ready for main PM review and merge into master branch",
    )
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert str(sub_id) in body["remediate"]
    task_svc.submit_pm_review.assert_not_awaited()


# ---------------------------------------------------------------------------
# MegaTask umbrella: no PR assembly + branchless completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_root_rejects_batch_umbrella() -> None:
    """A MegaTask umbrella assembles no PR of its own — submit_root must
    hard-reject it (each root-subtask PRs itself) so it never enters the
    in-path review gate."""
    pm_id = uuid4()
    umbrella_id = uuid4()
    t = MagicMock(
        id=umbrella_id,
        status="in_progress",
        assigned_to=pm_id,
        parent_task_id=None,
        batch_id=uuid4(),
        branch_name=None,
        team="main_pm",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="main_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.submit_root(pm_id, umbrella_id, "all root-subtasks shipped")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "no PR" in body["message"]
    assert "complete(" in body["remediate"]


@pytest.mark.asyncio
async def test_main_pm_complete_allows_batch_umbrella_from_in_progress() -> None:
    """A MegaTask umbrella is branchless: with every root-subtask terminal it
    completes straight from in_progress (no submit_root / PR), walking to
    awaiting_pm_review and escalating to the CEO — the PR requirement is waived."""
    pm_id = uuid4()
    umbrella_id = uuid4()
    t = MagicMock(
        id=umbrella_id,
        status="in_progress",
        assigned_to=pm_id,
        parent_task_id=None,
        batch_id=uuid4(),
        branch_name=None,
        team="main_pm",
    )
    escalated = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.get_subtasks.return_value = []
    task_svc.submit_pm_review.return_value = MagicMock(status="awaiting_pm_review")
    task_svc.escalate_to_ceo.return_value = escalated
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.main_pm_complete(
        pm_id, umbrella_id, "every root-subtask is terminal; MegaTask ready for CEO"
    )
    assert env.error is None
    # Branchless walk in_progress -> awaiting_pm_review, then escalate. No PR.
    task_svc.submit_pm_review.assert_awaited_once()
    task_svc.escalate_to_ceo.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_pm_complete_handles_escalate_returning_none() -> None:
    """Defense-in-depth: if escalate_to_ceo refuses (returns None), the verb
    surfaces an invalid_state rejection instead of dereferencing None."""
    pm_id = uuid4()
    umbrella_id = uuid4()
    t = MagicMock(
        id=umbrella_id,
        status="in_progress",
        assigned_to=pm_id,
        parent_task_id=None,
        batch_id=uuid4(),
        branch_name=None,
        team="main_pm",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.get_subtasks.return_value = []
    task_svc.submit_pm_review.return_value = MagicMock(status="awaiting_pm_review")
    task_svc.escalate_to_ceo.return_value = None  # refused
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.main_pm_complete(pm_id, umbrella_id, "ready for CEO sign-off")
    assert env.error == "invalid_state"
    assert "escalate_to_ceo" in env.as_dict()["message"]


@pytest.mark.asyncio
async def test_complete_escalates_batch_umbrella_from_in_progress() -> None:
    """A MegaTask umbrella is branchless by design and sits in ``in_progress``
    with no branch/PR; the ``complete`` verb's spec gate
    (``source_statuses={AWAITING_PM_REVIEW}``) must NOT reject it — the Main
    PM routes through ``main_pm_complete``, walking in_progress ->
    awaiting_pm_review -> awaiting_ceo_approval. Calling the ``complete``
    ENTRY point (not ``main_pm_complete`` directly) must succeed and escalate
    to the CEO; this exercises the real spec gate (``can_invoke_intent`` is
    pure)."""
    pm_id = uuid4()
    umbrella_id = uuid4()
    batch_id = uuid4()
    t = MagicMock(
        id=umbrella_id,
        status="in_progress",
        assigned_to=pm_id,
        parent_task_id=None,
        batch_id=batch_id,
        branch_name=None,
        pr_number=None,
        pr_created=False,
        pr_url=None,
        team="main_pm",
    )
    after_review = MagicMock(
        id=umbrella_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        parent_task_id=None,
        batch_id=batch_id,
        branch_name=None,
        team="main_pm",
    )
    escalated = MagicMock(
        id=umbrella_id,
        status="awaiting_ceo_approval",
        assigned_to=pm_id,
        parent_task_id=None,
        batch_id=batch_id,
        branch_name=None,
        team="main_pm",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="main_pm", slug="main-pm")
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.get_subtasks.return_value = []
    task_svc.submit_pm_review.return_value = after_review
    task_svc.escalate_to_ceo.return_value = escalated
    task_svc.reassign = AsyncMock()
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.complete(
        pm_id, umbrella_id, notes="every root-subtask terminal; MegaTask ready for CEO"
    )
    body = env.as_dict()
    assert body.get("error") is None, body
    assert body["status"] == "awaiting_ceo_approval"
    task_svc.submit_pm_review.assert_awaited_once()
    task_svc.escalate_to_ceo.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_rejects_non_batch_branchless_in_progress_root() -> None:
    """#30 negative pin: the ``_is_umbrella_in_progress`` bypass is gated on a
    genuine batch umbrella (``is_batch_umbrella`` = batch_id set AND no parent).
    A NON-batch branchless Main-PM root (a product-only delivery root,
    ``batch_id=None``) sitting in ``in_progress`` must NOT bypass the complete
    spec gate — the gate enforces ``source_statuses={AWAITING_PM_REVIEW}`` and
    rejects ``in_progress`` with ``invalid_state`` rather than falling through
    to ``main_pm_complete``. A regression that loosened the predicate (e.g.
    keying on ``branch_name is None`` alone) would let any branchless root
    walk past the awaiting_pm_review checkpoint; this pins the tight predicate."""
    pm_id = uuid4()
    root_id = uuid4()
    t = MagicMock(
        id=root_id,
        status="in_progress",
        assigned_to=pm_id,
        parent_task_id=None,
        batch_id=None,  # NOT a batch umbrella -> bypass must NOT fire.
        branch_name=None,
        pr_number=None,
        pr_created=False,
        pr_url=None,
        team="main_pm",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="main_pm", slug="main-pm")
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.get_subtasks.return_value = []
    task_svc.submit_pm_review = AsyncMock()
    task_svc.escalate_to_ceo = AsyncMock()
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.complete(
        pm_id, root_id, notes="non-batch branchless root is not awaiting_pm_review"
    )
    body = env.as_dict()
    # The spec gate ran (not bypassed) and rejected the in_progress source status.
    assert body.get("error") == "invalid_state", body
    # The bypass did NOT fire -> main_pm_complete never ran.
    task_svc.submit_pm_review.assert_not_awaited()
    task_svc.escalate_to_ceo.assert_not_awaited()

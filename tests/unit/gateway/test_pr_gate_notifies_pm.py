"""pr_fail delivers its change-requests to the owning PM, not just to GitHub.

The in-path ``pr_fail`` gate persists the reviewer's verdict to
``notes_structured.pr_review`` and posts it on the assembled PR — but historically
never pushed the concrete issues to any channel the owning PM reads (no a2a,
and ``_briefing_for`` / ``build_task_handoff`` read neither ``pr_reviewer_notes``
nor ``notes_structured.pr_review``). So the cell PM respawned into
``needs_revision`` saw a generic "needs revision" with zero actionable issues,
concluded there was nothing to rework, and re-submitted the same PR — an
infinite ``pr_fail`` loop (observed live on coordination root 9980d0a0 / PR #138).

The fix mirrors QA's ``fail_review`` a2a to the dev (qa.py:671-678): on
``pr_fail`` the gate now sends an a2a to the owner the runner just re-assigned
(the cell PM, via ``_revision_pm_for_task``) carrying the issues, so the PM
"knows" and can ``delegate`` a rework subtask or action the change-requests
directly. ``pr_pass`` is unaffected.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy import lifecycle as spec_module
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_choreographer() -> Choreographer:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    return Choreographer(ChoreographerDeps(**base))


def _stub_gate_path(
    c: Choreographer,
    *,
    reviewer_id: Any,
    t_before: Any,
    t_after: Any,
) -> None:
    """Drive ``_gate_decision`` past preflight/tracing/post and into the new
    a2a step without exercising the heavy ownership/tracing logic (those have
    their own tests). The runner rebinding to ``t_after`` is what simulates the
    PM re-assignment the real ``_revision_pm_for_task`` performs.
    """
    agent = MagicMock(role="pr_reviewer", slug="be-pr-reviewer")
    # Alias to ``Any`` so attribute assignment needs no type:ignore (mypy
    # doesn't flag attribute assignment on ``Any``; avoids ruff B010's
    # no-setattr rule too). ``object.__setattr__`` would also work but loses
    # the cross-scope narrowing callers rely on for ``cc.<attr>`` asserts.
    cc: Any = c
    cc._gate_preflight = AsyncMock(
        return_value=(
            t_before,
            agent,
            "pr_reviewer",
            {},
            spec_module.Context(actor_id=reviewer_id),
        )
    )
    cc._gate_tracing = AsyncMock(return_value=None)
    # These tests exercise the pr_fail a2a / notify path, not the head-sha
    # capture (which has its own suite in test_submit_root_unchanged_pr_guard).
    # Stub the capture so it does not walk the mock session into un-awaited
    # coroutines; the verdict still lands via the _record_gate_verdict spy.
    cc._capture_pr_head_sha = AsyncMock(return_value=None)
    cc._record_gate_verdict = MagicMock()
    cc._post_gate_review_to_pr = AsyncMock()
    runner = MagicMock()
    runner.run_intent = AsyncMock(return_value=t_after)
    cc._verb_runner = MagicMock(return_value=runner)


@pytest.mark.asyncio
async def test_pr_fail_notifies_reassigned_owning_pm() -> None:
    reviewer_id = uuid4()
    pm_id = uuid4()
    task_id = uuid4()
    parent_id = uuid4()
    t_before = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,  # owned by the reviewer until the runner runs
        pr_number=138,
        parent_task_id=parent_id,
        status="awaiting_pr_review",
    )
    # The runner reassigns the task to the owning PM (needs_revision owner).
    t_after = MagicMock(
        id=task_id,
        assigned_to=pm_id,
        pr_number=138,
        parent_task_id=parent_id,
        status="needs_revision",
    )

    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)

    await c.pr_fail(reviewer_id, task_id, ["seam mismatch", "docs lag the diff"])

    c.a2a.send.assert_awaited_once()
    kwargs = c.a2a.send.await_args.kwargs
    assert kwargs["from_agent"] == reviewer_id
    assert kwargs["to_agent"] == pm_id
    assert kwargs["skill"] == "code_review"
    assert kwargs["task_id"] == task_id
    body = kwargs["body"]
    assert "PR review needs changes." in body
    assert "seam mismatch" in body
    assert "docs lag the diff" in body


@pytest.mark.asyncio
async def test_pr_pass_does_not_notify_anyone() -> None:
    reviewer_id = uuid4()
    pm_id = uuid4()
    task_id = uuid4()
    t_before = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,
        pr_number=42,
        parent_task_id=uuid4(),
        status="awaiting_pr_review",
    )
    t_after = MagicMock(
        id=task_id, assigned_to=pm_id, pr_number=42, status="awaiting_pm_review"
    )

    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)

    await c.pr_pass(reviewer_id, task_id, "Assembled root scope is clean and covered.")

    c.a2a.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_fail_skips_a2a_when_no_assignee() -> None:
    """If the runner left the task unassigned, there's nobody to notify — must
    not raise and must not crash on ``a2a.send(None)``."""
    reviewer_id = uuid4()
    task_id = uuid4()
    t_before = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,
        pr_number=9,
        parent_task_id=uuid4(),
        status="awaiting_pr_review",
    )
    t_after = MagicMock(
        id=task_id, assigned_to=None, pr_number=9, status="needs_revision"
    )

    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)

    env = await c.pr_fail(reviewer_id, task_id, ["one concrete issue here"])
    c.a2a.send.assert_not_awaited()
    assert env.status == "needs_revision"


@pytest.mark.asyncio
async def test_pr_fail_a2a_for_main_pm_root_steers_to_redelegate() -> None:
    """A Main-PM branch-bearing root is an assembled cell→root / root→master PR —
    coordination, not the Main PM's own code. The ``pr_fail`` a2a body must steer
    the Main PM to re-delegate the fixes and NOT re-submit the unchanged root
    (the 2026-06-27 infinite ``pr_fail`` loop), while still carrying the concrete
    issues. ``_revision_pm_for_task`` returns main-pm for a non-cell team, so the
    recipient stays the Main PM — correct, since the Main PM re-delegates."""
    reviewer_id = uuid4()
    main_pm_id = uuid4()
    task_id = uuid4()
    t_before = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,
        pr_number=139,
        parent_task_id=uuid4(),
        status="awaiting_pr_review",
    )
    t_after = MagicMock(
        id=task_id,
        assigned_to=main_pm_id,
        pr_number=139,
        parent_task_id=uuid4(),
        status="needs_revision",
    )
    # Team is read off the runner-rebound task. ``getattr(team, "value", team)``
    # must yield ``"main_pm"``; a MagicMock team would not, so set the attribute
    # explicitly. branch_name set => an assembled root, not a branchless umbrella.
    t_after.team = spec_module.Team.MAIN_PM
    t_after.branch_name = "feature/main_pm/c80e19ff"

    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)

    await c.pr_fail(reviewer_id, task_id, ["duplicate TimeseriesChart export"])

    c.a2a.send.assert_awaited_once()
    kwargs = c.a2a.send.await_args.kwargs
    assert kwargs["to_agent"] == main_pm_id
    body = kwargs["body"]
    assert "PR review needs changes." in body
    assert "duplicate TimeseriesChart export" in body
    assert "re-delegate" in body
    assert "do NOT re-submit" in body


@pytest.mark.asyncio
async def test_pr_fail_a2a_failure_is_swallowed() -> None:
    """The gate transition already committed; an a2a delivery failure must not
    roll back the verdict or 500 the reviewer (same posture as the PR-post step,
    and the inverse of the cell_pm_complete None-deref crash)."""
    reviewer_id = uuid4()
    pm_id = uuid4()
    task_id = uuid4()
    t_before = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,
        pr_number=7,
        parent_task_id=uuid4(),
        status="awaiting_pr_review",
    )
    t_after = MagicMock(
        id=task_id, assigned_to=pm_id, pr_number=7, status="needs_revision"
    )

    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    c.a2a.send = AsyncMock(side_effect=RuntimeError("db hiccup"))

    env = await c.pr_fail(reviewer_id, task_id, ["a concrete actionable issue"])
    # Verdict still landed — the owning PM is in needs_revision.
    assert env.status == "needs_revision"


@pytest.mark.asyncio
async def test_pr_fail_returns_invalid_state_when_runner_returns_none() -> None:
    """A concurrent transition (cancel or racing reviewer) moving the task
    out of ``awaiting_pr_review`` after the gate makes ``run_intent`` return
    None; ``_gate_decision`` must surface ``invalid_state`` rather than
    dereference None and 500 on ``t.assigned_to`` / ``t.status``.
    """
    reviewer_id = uuid4()
    task_id = uuid4()
    t_before = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,
        pr_number=44,
        parent_task_id=uuid4(),
        status="awaiting_pr_review",
    )

    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)

    env = await c.pr_fail(reviewer_id, task_id, ["a concrete actionable issue"])

    # Clean rejection, not a 500.
    assert env.error == "invalid_state"
    # No PR post / no a2a against a None task. Alias to ``Any`` so the asserts
    # resolve without the cross-scope narrowing _stub_gate_path's assignment
    # can't provide (and without a type:ignore).
    cc: Any = c
    cc._post_gate_review_to_pr.assert_not_awaited()
    cc.a2a.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_pass_returns_invalid_state_when_runner_returns_none() -> None:
    """The same None-guard covers pr_pass — a concurrent cancel between
    gate and runner must surface invalid_state, not crash on ``str(t.status)``.
    """
    reviewer_id = uuid4()
    task_id = uuid4()
    t_before = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,
        pr_number=45,
        parent_task_id=uuid4(),
        status="awaiting_pr_review",
    )

    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)

    env = await c.pr_pass(
        reviewer_id, task_id, "Assembled root scope is clean and covered."
    )

    assert env.error == "invalid_state"
    # Alias to ``Any`` so the asserts resolve without cross-scope narrowing
    # (and without a type:ignore) — see test_pr_fail_returns_invalid_state...
    cc: Any = c
    cc._post_gate_review_to_pr.assert_not_awaited()
    cc.a2a.send.assert_not_awaited()

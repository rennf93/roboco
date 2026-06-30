"""claim_gate_review skips the dev claim guards.

A pr_reviewer claiming an awaiting_pr_review gate task does NOT transition
(status stays awaiting_pr_review — it is an inspection claim, not work-start).
The single-active-task / lane dev claim guards (``already_active`` /
``paused`` / ``_lane_claim_guard``) gate a DEVELOPER starting a code task; a
reviewer inspecting an assembled PR is not that, and applying them can stall
the in-path gate (e.g. a reviewer mid an external ``post_pr_review`` whose
in_progress task trips ``already_active`` could not claim the gate review).
The dependency guard still applies (do not review a gate whose deps are
unmet). See #192.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(task: AsyncMock) -> ChoreographerDeps:
    return ChoreographerDeps(
        task=task,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=AsyncMock(),
    )


def _gate_task(*, dependency_ids: list[Any] | None = None) -> Any:
    return MagicMock(
        id=uuid4(),
        status="awaiting_pr_review",
        assigned_to=uuid4(),
        parent_task_id=uuid4(),
        task_type="planning",
        dependency_ids=dependency_ids or [],
        team="main_pm",
        pr_number=139,
        pr_url="https://example/pr/139",
        branch_name="feature/main_pm/root",
        batch_id=None,
    )


def _wire_claim(c: Choreographer, task_svc: AsyncMock, *, t: Any) -> None:
    """Drive claim_gate_review past not-found / role / spec gate so the claim
    guard is the thing under test; stub the evidence build (hits git)."""
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        role="pr_reviewer", slug="be-pr-reviewer"
    )
    task_svc.pr_gate_claim = AsyncMock(return_value=t)
    cc: Any = c
    cc._build_gate_review_evidence = AsyncMock(return_value={"pr_number": 139})


@pytest.mark.asyncio
async def test_claim_gate_review_skips_already_active_guard() -> None:
    """A reviewer mid an external post_pr_review (an in_progress task) must NOT
    be blocked from claiming the gate review — the single-active-task guard
    gates a developer starting work, not a reviewer inspecting an assembled PR
    (the gate claim does not transition)."""
    task_svc = AsyncMock()
    t = _gate_task()
    # The reviewer already has an in_progress task (an external PR review).
    task_svc.list_in_progress_for_agent.return_value = [
        MagicMock(id=uuid4(), status="in_progress")
    ]
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    task_svc.has_earlier_incomplete_code_sibling.return_value = False
    c = Choreographer(_make_deps(task_svc))
    _wire_claim(c, task_svc, t=t)

    env = await c.claim_gate_review(uuid4(), t.id)
    body = env.as_dict()
    assert body.get("error") is None, body
    task_svc.pr_gate_claim.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_gate_review_skips_lane_guard() -> None:
    """The code-lane barrier gates a developer's code-leaf queue order; a gate
    review is not a code leaf the reviewer develops, so the lane guard must not
    refuse it even if the predicate were to return True for the task."""
    task_svc = AsyncMock()
    t = _gate_task()
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    # Lane predicate positively True — must NOT block a gate claim.
    task_svc.has_earlier_incomplete_code_sibling.return_value = True
    c = Choreographer(_make_deps(task_svc))
    _wire_claim(c, task_svc, t=t)

    env = await c.claim_gate_review(uuid4(), t.id)
    body = env.as_dict()
    assert body.get("error") is None, body
    # The lane guard never even ran for a gate claim.
    task_svc.has_earlier_incomplete_code_sibling.assert_not_awaited()
    task_svc.pr_gate_claim.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_gate_review_keeps_dependency_guard() -> None:
    """The dependency guard is not a dev-only invariant — a gate task whose
    dependencies are still unmet must still be refused (do not review a gate
    built on incomplete upstream work). Only the dev guards are skipped."""
    task_svc = AsyncMock()
    dep_id = uuid4()
    t = _gate_task(dependency_ids=[dep_id])
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[dep_id])
    task_svc.release_dependency_blocked_claim = AsyncMock()
    task_svc.has_earlier_incomplete_code_sibling.return_value = False
    c = Choreographer(_make_deps(task_svc))
    _wire_claim(c, task_svc, t=t)

    env = await c.claim_gate_review(uuid4(), t.id)
    body = env.as_dict()
    assert body.get("error") == "invalid_state", body
    task_svc.pr_gate_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_review_qa_keeps_already_active_guard() -> None:
    """Parity check: QA's claim_review (which DOES start review work) keeps the
    single-active-task guard — only the pr_reviewer GATE claim skips it. This
    pins that #192's carve-out is gate-claim-specific, not a blanket relax."""
    task_svc = AsyncMock()
    t = MagicMock(
        id=uuid4(),
        status="awaiting_qa",
        assigned_to=uuid4(),
        parent_task_id=uuid4(),
        task_type="code",
        dependency_ids=[],
        team="backend",
        pr_number=10,
        pr_url="https://example/pr/10",
        branch_name="feature/backend/abc",
        batch_id=None,
    )
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="qa", slug="be-qa")
    task_svc.list_in_progress_for_agent.return_value = [
        MagicMock(id=uuid4(), status="in_progress")
    ]
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    task_svc.has_earlier_incomplete_code_sibling.return_value = False
    task_svc.qa_claim = AsyncMock(return_value=t)
    c = Choreographer(_make_deps(task_svc))
    cc: Any = c
    cc._build_qa_review_evidence = AsyncMock(return_value={})

    env = await c.claim_review(uuid4(), t.id)
    body = env.as_dict()
    # QA mid another review -> already_active still blocks (kept).
    assert body.get("error") == "invalid_state", body
    task_svc.qa_claim.assert_not_awaited()

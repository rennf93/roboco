"""The in-path PR-review gate must not starve behind external PR review.

``pr-reviewer-1`` is shared between two duties: reviewing inbound
external/fork PRs (``_dispatch_pr_review_work``) and reviewing an assembled
root->master PR at the in-path gate (``_dispatch_pr_gate_work``). Both check
``_is_agent_active("pr-reviewer-1")`` before spawning, so within one dispatch
tick whichever dispatcher runs FIRST wins any contention for the shared
reviewer.

Live incident (2026-08): two cell-level gate tasks self-routed fine via
their own dedicated cell reviewers (be/fe/ux-pr-reviewer, no other duties),
while two Main-PM root-level gate tasks — sharing pr-reviewer-1 with the
external-PR queue — sat on assignee=main-pm until the CEO manually
intervened. ``_dispatch_all_work`` used to run ``pr_review_work`` (external)
before ``pr_gate_work`` (internal), so a tick with both kinds of work
pending for pr-reviewer-1 always let external win the race. The internal
gate blocks the whole delivery pipeline (every downstream PM merge waits on
it); an external PR can wait a tick. This test pins the corrected order.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator

# Every coroutine `_dispatch_all_work` awaits, in the source order it
# constructs the `dispatchers` list (see roboco/runtime/orchestrator.py).
_ALL_DISPATCH_METHODS = (
    "_dispatch_pm_work",
    "_dispatch_pm_closure_work",
    "_dispatch_revision_coordination_roots",
    "_dispatch_dev_work",
    "_dispatch_qa_work",
    "_dispatch_pr_gate_work",
    "_dispatch_pr_review_work",
    "_dispatch_doc_work",
    "_dispatch_pm_review_work",
    "_dispatch_marketing_work",
    "_dispatch_blocker_work",
    "_dispatch_claimed_without_agent",
    "_dispatch_escalation_work",
    "_dispatch_approval_work",
    "_dispatch_a2a_work",
    "_dispatch_audit_work",
    "_dispatch_vault_curation_work",
    "_detect_stuck_tasks",
)


def _orch_with_call_order() -> tuple[AgentOrchestrator, list[str]]:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    o = cast("Any", orch)
    order: list[str] = []

    def _tracker(name: str) -> Any:
        async def _inner(*_a: Any, **_kw: Any) -> None:
            order.append(name)

        return _inner

    for name in _ALL_DISPATCH_METHODS:
        setattr(o, name, _tracker(name))
    o._reap_stale_claims = AsyncMock()
    o._enforce_grok_cost_budget = AsyncMock()
    o._is_paused = AsyncMock(return_value=False)
    return orch, order


@pytest.mark.asyncio
async def test_pr_gate_work_dispatched_before_pr_review_work() -> None:
    """Internal in-path gate review must win the shared-reviewer race over
    external inbound PR review."""
    orch, order = _orch_with_call_order()

    await orch._dispatch_all_work()

    assert "_dispatch_pr_gate_work" in order
    assert "_dispatch_pr_review_work" in order
    assert order.index("_dispatch_pr_gate_work") < order.index(
        "_dispatch_pr_review_work"
    )


@pytest.mark.asyncio
async def test_pr_gate_work_still_runs_after_qa_and_dev_work() -> None:
    """Sanity check that the reorder didn't hoist pr_gate_work past
    unrelated dispatchers it has no ordering dependency on."""
    orch, order = _orch_with_call_order()

    await orch._dispatch_all_work()

    assert order.index("_dispatch_dev_work") < order.index("_dispatch_pr_gate_work")
    assert order.index("_dispatch_qa_work") < order.index("_dispatch_pr_gate_work")

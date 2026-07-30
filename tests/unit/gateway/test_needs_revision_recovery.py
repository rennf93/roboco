"""PM recovery of a rejected coordination task from needs_revision.

The in-path PR-review gate (and qa_fail / ceo_reject) can land a PM-owned
coordination/assembled task in ``needs_revision``. That state used to be
developer-claim-only, so the task had no actor and no exit but ``cancel`` — the
cell PM escalated in a loop. Now a PM re-claims its rejected task
(``i_will_plan``), revises the plan, and re-delegates the fixes.

Scope: ``pr_fail`` / ``qa_fail`` reassign the task to its owning PM, and
``give_me_work`` only ever offers an agent its OWN assigned tasks — so a PM is
never handed a developer's leaf, exactly as a developer is never handed a PM's
coordination root. (The scope lives in routing, not a gateway-only ownership
gate, which would break the spec=gateway parity invariant.)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy import lifecycle as spec
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

# --------------------------------------------------------------------------- #
# Lifecycle authority: PMs may now claim needs_revision
# --------------------------------------------------------------------------- #


def test_pm_can_claim_needs_revision() -> None:
    task = SimpleNamespace(
        status="needs_revision", task_type="planning", assigned_to=None
    )
    for role in (spec.Role.CELL_PM, spec.Role.MAIN_PM):
        assert spec.can_invoke_action(role, "claim", task).allowed
    # Developers still own leaf revisions; QA / documenter still cannot claim it.
    assert spec.can_invoke_action(spec.Role.DEVELOPER, "claim", task).allowed
    assert not spec.can_invoke_action(spec.Role.QA, "claim", task).allowed
    assert not spec.can_invoke_action(spec.Role.DOCUMENTER, "claim", task).allowed


def test_pm_claim_needs_revision_works_for_code_typed_root() -> None:
    # Main-PM coordination roots can be code-typed, so the claim must NOT be
    # task_type-gated — authority is status-based, scoped by routing.
    task = SimpleNamespace(status="needs_revision", task_type="code", assigned_to=None)
    assert spec.can_invoke_action(spec.Role.MAIN_PM, "claim", task).allowed


# --------------------------------------------------------------------------- #
# Routing scope: give_me_work offers a PM its OWN rejected coordination task
# --------------------------------------------------------------------------- #


def _make_deps(task_svc: AsyncMock) -> ChoreographerDeps:
    # Findings-ledger reads (ReviewFindingsRepository.list_for_task) go
    # through session.execute — an unconfigured AsyncMock's awaited result
    # is itself an AsyncMock, so a plain sync `.scalars()` call on it leaks
    # an unawaited coroutine. Empty scalars result (no findings).
    task_svc.session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )
    repo = AsyncMock()
    for m in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
        "company_goals",
    ):
        getattr(repo, m).return_value = []
    return ChoreographerDeps(
        task=task_svc,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=repo,
    )


@pytest.mark.asyncio
async def test_give_me_work_offers_pm_its_needs_revision_task() -> None:
    pm_id, task_id = uuid4(), uuid4()
    task = MagicMock(
        id=task_id, status="needs_revision", team="backend", dependency_ids=[]
    )
    task_svc = AsyncMock()
    task_svc.list_pending_for_agent.return_value = []
    task_svc.list_assigned_for_agent.return_value = [task]
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    c = Choreographer(_make_deps(task_svc))

    env = await c.give_me_work(pm_id)
    body = env.as_dict()

    assert body["task_id"] == str(task_id)
    # The PM is told to re-plan (revise) the rejected task, not a dev verb.
    assert "i_will_plan" in body["next"]

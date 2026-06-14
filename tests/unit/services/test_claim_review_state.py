"""Claiming an awaiting_pm_review task must NOT transition it to `claimed`.

awaiting_pm_review is a review state: the assigned PM's complete() requires the
task to be IN awaiting_pm_review. The dispatcher claims an *ownerless* review
task before spawning the PM; if that claim transitioned it to `claimed`, the PM
could never complete (observed live: a complete() rejected with invalid_state,
the task then bounced to blocked). claim_task_for_agent must therefore do a
no-transition review-claim for awaiting_pm_review, exactly like QA/Doc — while
leaving every other state (pending, needs_revision, ...) transitioning as before.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.base import AgentRole, TaskStatus
from roboco.services.task import TaskService


def _svc() -> TaskService:
    svc = TaskService.__new__(TaskService)
    svc.session = AsyncMock()
    return svc


def _agent() -> MagicMock:
    # A PM claiming for review — not QA/Documenter, so the self-review check is
    # skipped; can_perform_task_action is stubbed True on the permissions mock.
    return MagicMock(role=AgentRole.CELL_PM, agent_id=uuid4())


def _perms() -> MagicMock:
    p = MagicMock()
    p.can_perform_task_action = MagicMock(return_value=True)
    return p


@pytest.mark.asyncio
async def test_awaiting_pm_review_claim_does_not_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _svc()
    task_id = uuid4()
    task = MagicMock(status=TaskStatus.AWAITING_PM_REVIEW, team="backend")
    review_claim = AsyncMock(return_value=task)
    plain_claim = AsyncMock(return_value=task)
    monkeypatch.setattr(svc, "_load_task_or_raise", AsyncMock(return_value=task))
    monkeypatch.setattr(svc, "_qa_or_doc_claim", review_claim)
    monkeypatch.setattr(svc, "claim", plain_claim)
    agent = _agent()

    await svc.claim_task_for_agent(task_id, agent, _perms(), claim_target_slug=None)

    # No-transition review-claim used; the transitioning claim() never called.
    review_claim.assert_awaited_once_with(
        agent.agent_id, task_id, TaskStatus.AWAITING_PM_REVIEW
    )
    plain_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_claim_still_transitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: non-review states still go through the normal claim()."""
    svc = _svc()
    task_id = uuid4()
    task = MagicMock(status=TaskStatus.PENDING, team="backend")
    review_claim = AsyncMock(return_value=task)
    plain_claim = AsyncMock(return_value=task)
    monkeypatch.setattr(svc, "_load_task_or_raise", AsyncMock(return_value=task))
    monkeypatch.setattr(svc, "_qa_or_doc_claim", review_claim)
    monkeypatch.setattr(svc, "claim", plain_claim)

    await svc.claim_task_for_agent(task_id, _agent(), _perms(), claim_target_slug=None)

    plain_claim.assert_awaited_once()
    review_claim.assert_not_awaited()

"""Same-agent retry and different-agent refusal for the three claim-then-assemble verbs.

The durability boundary fix commits the claim BEFORE evidence assembly. This
file pins the two behavioral consequences:

1. **Same-agent retry**: when the task is already claimed by the calling agent
   (a prior attempt committed the claim but the evidence assembly timed out),
   the verb skips the re-claim and routes straight to evidence rebuild —
   returning success with the evidence payload, not a conflict.

2. **Different-agent refusal**: a different agent attempting to claim a task
   already claimed by someone else is still refused. ``claim_review`` and
   ``claim_doc_task`` emit ``not_authorized``; ``claim_gate_review`` emits
   ``invalid_state`` (its pre-existing envelope shape).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

_QA_PR = 8
_GATE_PR = 42


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
    base["task"].session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )
    base["task"].session.commit = AsyncMock()
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
    return ChoreographerDeps(**base)


# ---------------------------------------------------------------------------
# claim_review — same-agent retry returns evidence, different-agent refused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_review_same_agent_retry_returns_evidence() -> None:
    """A second claim_review by the SAME agent (already claimed by it) skips
    the re-claim and returns success with the evidence payload."""
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_qa",
        assigned_to=qa_id,
        active_claimant_id=qa_id,
        pr_number=_QA_PR,
        pr_url="https://x/pr/8",
        commits=[],
        team="backend",
        branch_name="feature/backend/abc",
        work_session_id=None,
        documents=[],
        dev_notes="",
        acceptance_criteria=[],
        acceptance_criteria_status=[],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="qa", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("", [])
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)
    cc: Any = c
    ev_mock = MagicMock()
    ev_mock.as_dict.return_value = {"pr_number": _QA_PR, "files_changed": []}
    cc._build_qa_claim_evidence = AsyncMock(return_value=ev_mock)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()

    assert body["error"] is None, body
    assert body["evidence"]["pr_number"] == _QA_PR
    # The claim was NOT re-issued — same agent already holds it.
    task_svc.qa_claim.assert_not_awaited()
    # Evidence WAS rebuilt.
    cc._build_qa_claim_evidence.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_review_different_agent_refused() -> None:
    """A different agent attempting to claim a task already claimed by someone
    else is refused with not_authorized."""
    qa_a = uuid4()
    qa_b = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_qa",
        assigned_to=qa_a,
        active_claimant_id=qa_a,
        pr_number=_QA_PR,
        pr_url="https://x/pr/8",
        commits=[],
        team="backend",
        branch_name="feature/backend/abc",
        work_session_id=None,
        documents=[],
        dev_notes="",
        acceptance_criteria=[],
        acceptance_criteria_status=[],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="qa", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    # qa_claim returns None: _qa_or_doc_claim blocks a different agent.
    task_svc.qa_claim = AsyncMock(return_value=None)
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("", [])
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)
    cc: Any = c
    cc._build_qa_claim_evidence = AsyncMock(return_value={})

    env = await c.claim_review(qa_b, task_id)
    body = env.as_dict()

    assert body["error"] == "not_authorized", body
    assert "already claimed" in body["message"].lower()
    task_svc.qa_claim.assert_awaited_once()
    # Evidence was NOT built — the rejection short-circuited.
    cc._build_qa_claim_evidence.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_review_commits_before_evidence_assembly() -> None:
    """The durability-boundary ordering itself, not just its two behavioral
    consequences: on a genuine (not-yet-claimed) claim, ``mark_evidence_
    inspected`` + ``session.commit`` must both happen BEFORE the evidence
    builder is invoked — a cancelled evidence assembly must never be able to
    discard an uncommitted claim."""
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_qa",
        assigned_to=qa_id,
        active_claimant_id=None,  # not yet claimed -> the real claim path runs
        pr_number=_QA_PR,
        pr_url="https://x/pr/8",
        commits=[],
        team="backend",
        branch_name="feature/backend/abc",
        work_session_id=None,
        documents=[],
        dev_notes="",
        acceptance_criteria=[],
        acceptance_criteria_status=[],
    )
    claimed_t = MagicMock(
        id=task_id,
        status="awaiting_qa",
        assigned_to=qa_id,
        active_claimant_id=qa_id,
        pr_number=_QA_PR,
        pr_url="https://x/pr/8",
        commits=[],
        team="backend",
        branch_name="feature/backend/abc",
        work_session_id=None,
        documents=[],
        dev_notes="",
        acceptance_criteria=[],
        acceptance_criteria_status=[],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="qa", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.qa_claim = AsyncMock(return_value=claimed_t)
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("", [])
    deps = _make_deps(task=task_svc, git=git_svc)

    call_order: list[str] = []
    task_svc.mark_evidence_inspected.side_effect = lambda *_a, **_k: call_order.append(
        "mark_evidence_inspected"
    )
    task_svc.session.commit.side_effect = lambda *_a, **_k: call_order.append("commit")

    c = Choreographer(deps)
    cc: Any = c
    ev_mock = MagicMock()
    ev_mock.as_dict.return_value = {"pr_number": _QA_PR, "files_changed": []}

    async def _build_evidence(*_a: Any, **_k: Any) -> Any:
        call_order.append("build_evidence")
        return ev_mock

    cc._build_qa_claim_evidence = _build_evidence

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()

    assert body["error"] is None, body
    task_svc.qa_claim.assert_awaited_once()
    # qa_evidence_inspected rides the SAME commit as the claim, and both
    # happen strictly before the evidence builder runs.
    assert call_order == ["mark_evidence_inspected", "commit", "build_evidence"]


# ---------------------------------------------------------------------------
# claim_gate_review — same-agent retry returns evidence, different-agent refused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_gate_review_same_agent_retry_returns_evidence() -> None:
    """A second claim_gate_review by the SAME reviewer skips re-claim and
    returns success with the evidence payload."""
    reviewer_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pr_review",
        assigned_to=reviewer_id,
        active_claimant_id=reviewer_id,
        pr_number=_GATE_PR,
        pr_url="https://x/pr/42",
        branch_name="feature/main_pm/abc",
        parent_task_id=None,
        batch_id=None,
        acceptance_criteria=["AC1"],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="pr_reviewer", team=None)
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    cc: Any = c
    cc._build_gate_review_evidence = AsyncMock(
        return_value={"pr_number": _GATE_PR, "diff": "+++ diff"}
    )

    env = await c.claim_gate_review(reviewer_id, task_id)
    body = env.as_dict()

    assert body["error"] is None, body
    assert body["evidence"]["pr_number"] == _GATE_PR
    # The claim was NOT re-issued.
    task_svc.pr_gate_claim.assert_not_awaited()
    cc._build_gate_review_evidence.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_gate_review_different_agent_refused() -> None:
    """A different reviewer attempting to claim a gate task already claimed by
    someone else is refused with invalid_state."""
    reviewer_a = uuid4()
    reviewer_b = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pr_review",
        assigned_to=reviewer_a,
        active_claimant_id=reviewer_a,
        pr_number=_GATE_PR,
        pr_url="https://x/pr/42",
        branch_name="feature/main_pm/abc",
        parent_task_id=None,
        batch_id=None,
        acceptance_criteria=["AC1"],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="pr_reviewer", team=None)
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.pr_gate_claim = AsyncMock(return_value=None)
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    cc: Any = c
    cc._build_gate_review_evidence = AsyncMock(return_value={})

    env = await c.claim_gate_review(reviewer_b, task_id)
    body = env.as_dict()

    assert body["error"] == "invalid_state", body
    assert "no longer claimable" in body["message"].lower()
    task_svc.pr_gate_claim.assert_awaited_once()
    cc._build_gate_review_evidence.assert_not_awaited()


# ---------------------------------------------------------------------------
# claim_doc_task — same-agent retry returns evidence, different-agent refused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_doc_task_same_agent_retry_returns_evidence() -> None:
    """A second claim_doc_task by the SAME documenter skips re-claim and
    returns success with the evidence payload."""
    doc_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_documentation",
        assigned_to=doc_id,
        active_claimant_id=doc_id,
        pr_number=8,
        pr_url="https://github.com/x/y/pull/8",
        commits=[{"sha": "abc", "message": "feat: x"}],
        team="backend",
        branch_name="feature/backend/abc--def",
        work_session_id=uuid4(),
        documents=[],
        dev_notes="",
        acceptance_criteria=[],
        acceptance_criteria_status=[],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="documenter", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    git_svc = AsyncMock()
    git_svc.diff.return_value = "+++ diff"
    git_svc.list_changed_files.return_value = ["README.md"]
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()

    assert body["error"] is None, body
    assert body["evidence"]["pr_url"] == "https://github.com/x/y/pull/8"
    # The claim was NOT re-issued.
    task_svc.doc_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_doc_task_different_agent_refused() -> None:
    """A different documenter attempting to claim a task already claimed by
    someone else is refused with not_authorized."""
    doc_a = uuid4()
    doc_b = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_documentation",
        assigned_to=doc_a,
        active_claimant_id=doc_a,
        pr_number=8,
        pr_url="https://github.com/x/y/pull/8",
        commits=[{"sha": "abc", "message": "feat: x"}],
        team="backend",
        branch_name="feature/backend/abc--def",
        work_session_id=uuid4(),
        documents=[],
        dev_notes="",
        acceptance_criteria=[],
        acceptance_criteria_status=[],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="documenter", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.doc_claim = AsyncMock(return_value=None)
    git_svc = AsyncMock()
    git_svc.diff.return_value = "+++ diff"
    git_svc.list_changed_files.return_value = ["README.md"]
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.claim_doc_task(doc_b, task_id)
    body = env.as_dict()

    assert body["error"] == "not_authorized", body
    assert "already claimed" in body["message"].lower()
    task_svc.doc_claim.assert_awaited_once()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

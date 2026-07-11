"""Wiring tests for the revision-findings ledger's delivery to reviewers +
the verified-stamping semantics.

- ``claim_review`` / ``claim_gate_review`` evidence carries ``prior_findings``
  (the full ledger, not just what's open) so a round-N+1 reviewer verifies
  prior rounds item-by-item.
- ``pass_review`` stamps ``qa``-origin addressed findings verified;
  ``pr_pass`` stamps ``pr_gate``-origin addressed findings verified. Both
  run BEFORE the transition (same-transaction posture) — a stamping failure
  must reject cleanly and never let the transition proceed against a stale
  ledger.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy import lifecycle as spec_module
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer import findings as findings_lib

_EXPECTED_TWO = 2


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
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _row(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid4(),
        "round": 1,
        "origin": "qa",
        "status": "addressed",
        "severity": "major",
        "file": "roboco/services/task.py",
        "line": 10,
        "expected": "raises",
        "actual": "swallows",
        "fix": "add raise",
        "evidence": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# claim_review — prior_findings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_review_evidence_carries_prior_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t_initial = MagicMock(
        id=task_id,
        status="awaiting_qa",
        assigned_to=None,
        pr_number=8,
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
    t_claimed = MagicMock(**{**t_initial.__dict__, "assigned_to": qa_id})
    task_svc = AsyncMock()
    task_svc.get.return_value = t_initial
    task_svc.agent_for.return_value = MagicMock(role="qa", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.qa_claim.return_value = t_claimed
    git_svc = AsyncMock()
    git_svc.diff.return_value = ""
    git_svc.list_changed_files.return_value = []
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    verified_row = _row(status="verified", round=1, actual="round 1 issue")
    open_row = _row(status="open", round=2, actual="round 2 issue")
    monkeypatch.setattr(
        findings_lib,
        "full_ledger_for_task",
        AsyncMock(return_value=[open_row, verified_row]),
    )
    monkeypatch.setattr(
        findings_lib, "open_findings_for_task", AsyncMock(return_value=[open_row])
    )

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    prior = body["evidence"]["prior_findings"]
    assert len(prior) == _EXPECTED_TWO
    assert {f["status"] for f in prior} == {"open", "verified"}
    assert body["evidence"]["revision_findings"] == [
        {
            "id": str(open_row.id)[:8],
            "round": 2,
            "origin": "qa",
            "status": "open",
            "severity": "major",
            "file": "roboco/services/task.py",
            "line": 10,
            "expected": "raises",
            "actual": "round 2 issue",
            "fix": "add raise",
            "evidence": None,
        }
    ]


# ---------------------------------------------------------------------------
# claim_gate_review — prior_findings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_gate_review_evidence_carries_prior_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewer_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pr_review",
        assigned_to=None,
        pr_number=42,
        pr_url="https://x/pr/42",
        branch_name="feature/main_pm/abc",
        parent_task_id=None,
        batch_id=None,
        acceptance_criteria=["AC1"],
    )
    t_claimed = MagicMock(**{**t.__dict__, "assigned_to": reviewer_id})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="pr_reviewer", team=None)
    task_svc.pr_gate_claim.return_value = t_claimed
    git_svc = AsyncMock()
    git_svc.diff.return_value = "+++ diff"
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    gate_row = _row(origin="pr_gate", status="open", actual="assembled diff issue")
    monkeypatch.setattr(
        findings_lib, "full_ledger_for_task", AsyncMock(return_value=[gate_row])
    )

    env = await c.claim_gate_review(reviewer_id, task_id)
    body = env.as_dict()
    prior = body["evidence"]["prior_findings"]
    assert len(prior) == 1
    assert prior[0]["origin"] == "pr_gate"
    assert prior[0]["actual"] == "assembled diff issue"


# ---------------------------------------------------------------------------
# pass_review — verified-stamp wiring + same-transaction failure semantics
# ---------------------------------------------------------------------------


def _qa_owned_task(task_id: Any, qa_id: Any, **overrides: Any) -> MagicMock:
    base = {
        "id": task_id,
        "status": "awaiting_qa",
        "task_type": "code",
        "team": "backend",
        "assigned_to": qa_id,
        "qa_evidence_inspected": True,
        "quick_context": None,
    }
    base.update(overrides)
    return MagicMock(**base)


@pytest.mark.asyncio
async def test_pass_review_stamps_qa_origin_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t = _qa_owned_task(task_id, qa_id)
    after = MagicMock(
        id=task_id, status="awaiting_documentation", assigned_to=qa_id, team="backend"
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="qa", team="backend", slug=None)
    task_svc.qa_pass.return_value = after
    task_svc.documenter_for_team.return_value = None
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    )
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    stamp = AsyncMock(return_value=1)
    monkeypatch.setattr(findings_lib, "stamp_addressed_verified", stamp)

    notes = (
        "Reviewed PR carefully. Branch convention correct. Commit prefix "
        "verified. README diff matches spec. All acceptance criteria met."
    )
    env = await c.pass_review(qa_id, task_id, notes=notes)
    assert env.error is None, env.as_dict()
    stamp.assert_awaited_once()
    call = stamp.await_args
    assert call is not None
    assert call.kwargs.get("origin") == "qa"
    task_svc.qa_pass.assert_awaited_once()


@pytest.mark.asyncio
async def test_pass_review_stamp_failure_rejects_before_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t = _qa_owned_task(task_id, qa_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="qa", team="backend", slug=None)
    task_svc.session = MagicMock()
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    monkeypatch.setattr(
        findings_lib,
        "stamp_addressed_verified",
        AsyncMock(side_effect=RuntimeError("ledger down")),
    )

    notes = (
        "Reviewed PR carefully. Branch convention correct. Commit prefix "
        "verified. README diff matches spec. All acceptance criteria met."
    )
    env = await c.pass_review(qa_id, task_id, notes=notes)
    assert env.error == "invalid_state"
    # The transition never ran — the ledger failure didn't leave a passed
    # task against a stale verified-stamp.
    task_svc.qa_pass.assert_not_awaited()


# ---------------------------------------------------------------------------
# pr_pass — verified-stamp wiring + same-transaction failure semantics
# ---------------------------------------------------------------------------


def _make_gate_choreographer() -> Choreographer:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base["task"].session = MagicMock()
    base["task"].session.add = MagicMock()
    base["task"].session.flush = AsyncMock()
    return Choreographer(ChoreographerDeps(**base))


def _stub_gate_path(
    c: Choreographer, *, reviewer_id: Any, t_before: Any, t_after: Any
) -> MagicMock:
    agent = MagicMock(role="pr_reviewer", slug="be-pr-reviewer")
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
    cc._project_slug_for = AsyncMock(return_value=None)
    record_spy = MagicMock()
    cc._record_gate_verdict = record_spy
    cc._post_gate_review_to_pr = AsyncMock()
    runner = MagicMock()
    runner.run_intent = AsyncMock(return_value=t_after)
    cc._verb_runner = MagicMock(return_value=runner)
    return record_spy


def _t(*, status: str = "awaiting_pr_review", pr_number: int | None = 42) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        assigned_to=None,
        pr_number=pr_number,
        parent_task_id=uuid4(),
        status=status,
    )


@pytest.mark.asyncio
async def test_pr_pass_stamps_pr_gate_origin_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewer_id = uuid4()
    t_before = _t()
    t_after = _t(status="awaiting_pm_review")
    c = _make_gate_choreographer()
    record_spy = _stub_gate_path(
        c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after
    )
    stamp = AsyncMock(return_value=1)
    monkeypatch.setattr(findings_lib, "stamp_addressed_verified", stamp)

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    stamp.assert_awaited_once()
    call = stamp.await_args
    assert call is not None
    assert call.kwargs.get("origin") == "pr_gate"
    record_spy.assert_called_once()


@pytest.mark.asyncio
async def test_pr_pass_stamp_failure_rejects_before_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewer_id = uuid4()
    t_before = _t()
    c = _make_gate_choreographer()
    record_spy = _stub_gate_path(
        c, reviewer_id=reviewer_id, t_before=t_before, t_after=None
    )
    monkeypatch.setattr(
        findings_lib,
        "stamp_addressed_verified",
        AsyncMock(side_effect=RuntimeError("ledger down")),
    )

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error == "invalid_state"
    # The verdict was never recorded and the transition never ran.
    record_spy.assert_not_called()


@pytest.mark.asyncio
async def test_pr_fail_does_not_stamp_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    """pr_fail never verifies findings — only pr_pass does."""
    reviewer_id = uuid4()
    t_before = _t()
    t_after = _t(status="needs_revision")
    c = _make_gate_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    stamp = AsyncMock()
    monkeypatch.setattr(findings_lib, "stamp_addressed_verified", stamp)

    env = await c.pr_fail(reviewer_id, t_before.id, ["a concrete actionable issue"])

    assert env.error is None, env.as_dict()
    stamp.assert_not_awaited()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

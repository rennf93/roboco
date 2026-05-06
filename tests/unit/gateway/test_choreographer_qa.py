"""Tests for QA-facing Choreographer methods."""

from __future__ import annotations

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


_EXPECTED_PR_NUMBER = 8
_EXPECTED_PR_URL = "https://github.com/x/y/pull/8"


@pytest.mark.asyncio
async def test_claim_review_returns_evidence_inline() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t_initial = MagicMock(
        id=task_id,
        status="awaiting_qa",
        assigned_to=None,
        pr_number=_EXPECTED_PR_NUMBER,
        pr_url=_EXPECTED_PR_URL,
        commits=[{"sha": "abc123", "message": "feat: x"}],
        team="backend",
        branch_name="feature/backend/abc--def",
        work_session_id=uuid4(),
        documents=[],
        dev_notes="implemented x",
        acceptance_criteria=["AC1"],
        acceptance_criteria_status=[
            {"criterion": "AC1", "referencing_artifact_id": "abc123"},
        ],
    )
    t_claimed = MagicMock(
        **{**t_initial.__dict__, "assigned_to": qa_id, "status": "claimed"},
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t_initial
    task_svc.agent_for.return_value = MagicMock(role="qa", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.qa_claim.return_value = t_claimed
    work_svc = AsyncMock()
    work_svc.files_changed.return_value = ["README.md"]
    git_svc = AsyncMock()
    git_svc.diff.return_value = "+++ diff content"
    deps = _make_deps(task=task_svc, work_session=work_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] is None
    assert body["evidence"]["pr_url"] == _EXPECTED_PR_URL
    assert body["evidence"]["pr_number"] == _EXPECTED_PR_NUMBER
    assert body["evidence"]["commits"][0]["sha"] == "abc123"
    assert "README.md" in body["evidence"]["files_changed"]


@pytest.mark.asyncio
async def test_claim_review_blocks_if_task_not_awaiting_qa() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="in_progress")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "awaiting_qa" in body["message"]


@pytest.mark.asyncio
async def test_claim_review_marks_evidence_inspected() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_qa",
        pr_number=8,
        pr_url="x",
        commits=[],
        team="backend",
        branch_name="feature/backend/abc",
        work_session_id=None,
        documents=[],
        dev_notes="",
        acceptance_criteria=[],
        acceptance_criteria_status=[],
    )
    t_claimed = MagicMock(**{**t.__dict__, "assigned_to": qa_id})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="qa", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.qa_claim.return_value = t_claimed
    git_svc = AsyncMock()
    git_svc.diff.return_value = ""
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    await c.claim_review(qa_id, task_id)
    task_svc.mark_evidence_inspected.assert_awaited_once_with(task_id)


@pytest.mark.asyncio
async def test_claim_review_task_not_found_returns_not_found() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] == "not_found"


@pytest.mark.asyncio
async def test_pass_review_task_not_found_returns_not_found() -> None:
    """Line 117 of qa.py: _verify_qa_owner emits not_found when task is None."""
    qa_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.pass_review(qa_id, task_id, notes="x" * 80)
    assert env.as_dict()["error"] == "not_found"


@pytest.mark.asyncio
async def test_pass_review_requires_qa_notes_min_chars() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="claimed",
        assigned_to=qa_id,
        qa_evidence_inspected=True,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.pass_review(qa_id, task_id, notes="too short")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "qa_notes>=min" in body["missing"]


@pytest.mark.asyncio
async def test_pass_review_requires_journal_learning() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="claimed",
        assigned_to=qa_id,
        qa_evidence_inspected=True,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = False
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    notes = "x" * 100  # long enough
    env = await c.pass_review(qa_id, task_id, notes=notes)
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:learning" in body["missing"]


@pytest.mark.asyncio
async def test_pass_review_requires_evidence_inspected() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="claimed",
        assigned_to=qa_id,
        qa_evidence_inspected=False,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    notes = "x" * 100
    env = await c.pass_review(qa_id, task_id, notes=notes)
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "qa_evidence_inspected" in body["missing"]


@pytest.mark.asyncio
async def test_pass_review_succeeds_and_transitions() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="claimed",
        assigned_to=qa_id,
        qa_evidence_inspected=True,
    )
    after = MagicMock(
        **{
            **t.__dict__,
            "status": "awaiting_documentation",
            "team": "backend",
            "pr_url": "https://x/pr/8",
        },
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.qa_pass.return_value = after
    task_svc.documenter_for_team.return_value = MagicMock(id=uuid4())
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    a2a_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc, a2a=a2a_svc)
    c = Choreographer(deps)

    notes = (
        "Reviewed PR carefully. Branch convention correct. Commit prefix "
        "verified. README diff matches spec. All acceptance criteria met."
    )
    env = await c.pass_review(qa_id, task_id, notes=notes)
    assert env.error is None
    assert env.status == "awaiting_documentation"
    task_svc.qa_pass.assert_awaited_once()
    a2a_svc.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_pass_review_not_assigned_returns_not_authorized() -> None:
    qa_id = uuid4()
    other = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="claimed", assigned_to=other, qa_evidence_inspected=True
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.pass_review(qa_id, task_id, notes="x")
    body = env.as_dict()
    assert body["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_fail_review_succeeds() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    dev_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="claimed",
        assigned_to=qa_id,
        qa_evidence_inspected=True,
    )
    after = MagicMock(
        **{**t.__dict__, "status": "needs_revision", "assigned_to": dev_id},
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.qa_fail.return_value = after
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    a2a_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc, a2a=a2a_svc)
    c = Choreographer(deps)

    issues = [
        "Missing unit test coverage for /healthz endpoint — add at least one assertion",
        "Lint errors in /api/foo.py: unused import and missing return type annotation",
    ]
    env = await c.fail_review(qa_id, task_id, issues)
    assert env.error is None
    assert env.status == "needs_revision"
    task_svc.qa_fail.assert_awaited_once()
    a2a_svc.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_fail_review_requires_at_least_one_issue() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="claimed",
        assigned_to=qa_id,
        qa_evidence_inspected=True,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.fail_review(qa_id, task_id, issues=[])
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "issue" in body["message"].lower()


@pytest.mark.asyncio
async def test_fail_review_not_assigned_returns_not_authorized() -> None:
    qa_id = uuid4()
    other = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="claimed", assigned_to=other, qa_evidence_inspected=True
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.fail_review(qa_id, task_id, issues=["x"])
    body = env.as_dict()
    assert body["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_fail_review_blocks_when_journal_learning_missing() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="claimed",
        assigned_to=qa_id,
        qa_evidence_inspected=True,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = False  # no learning
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.fail_review(qa_id, task_id, issues=["x" * 20])
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:learning" in body["missing"]

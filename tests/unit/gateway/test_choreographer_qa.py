"""Tests for QA-facing Choreographer methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides):
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
async def test_claim_review_returns_evidence_inline():
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
async def test_claim_review_blocks_if_task_not_awaiting_qa():
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
async def test_claim_review_marks_evidence_inspected():
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
    task_svc.qa_claim.return_value = t_claimed
    git_svc = AsyncMock()
    git_svc.diff.return_value = ""
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    await c.claim_review(qa_id, task_id)
    task_svc.mark_evidence_inspected.assert_awaited_once_with(task_id)


@pytest.mark.asyncio
async def test_claim_review_task_not_found_returns_not_found():
    qa_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] == "not_found"

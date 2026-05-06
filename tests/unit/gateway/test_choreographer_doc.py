"""Tests for Documenter Choreographer methods."""

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


@pytest.mark.asyncio
async def test_claim_doc_task_returns_evidence() -> None:
    doc_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_documentation",
        assigned_to=None,
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
    after = MagicMock(**{**t.__dict__, "assigned_to": doc_id})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="documenter", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.doc_claim.return_value = after
    work_svc = AsyncMock()
    work_svc.files_changed.return_value = ["README.md"]
    git_svc = AsyncMock()
    git_svc.diff.return_value = "+++ diff"
    deps = _make_deps(task=task_svc, work_session=work_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    assert body["error"] is None
    assert body["evidence"]["pr_url"] == "https://github.com/x/y/pull/8"
    assert "README.md" in body["evidence"]["files_changed"]


@pytest.mark.asyncio
async def test_claim_doc_task_blocks_wrong_state() -> None:
    doc_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="in_progress")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_claim_doc_task_not_found() -> None:
    doc_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_doc_task(doc_id, task_id)
    assert env.as_dict()["error"] == "not_found"


@pytest.mark.asyncio
async def test_i_documented_requires_min_notes() -> None:
    doc_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="claimed", assigned_to=doc_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_documented(doc_id, task_id, notes="short", files=["a.md"])
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "docs_notes>=20" in body["missing"]


@pytest.mark.asyncio
async def test_i_documented_task_not_found() -> None:
    """Line 105: task missing → not_found via _emit_rejection."""
    doc_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    env = await c.i_documented(doc_id, task_id, notes="x" * 30, files=["docs.md"])
    assert env.as_dict()["error"] == "not_found"


@pytest.mark.asyncio
async def test_i_documented_requires_files() -> None:
    doc_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="claimed", assigned_to=doc_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    notes = "Wrote backend/guides/feature-x.md with usage examples."
    env = await c.i_documented(doc_id, task_id, notes=notes, files=[])
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "files" in body["missing"]


@pytest.mark.asyncio
async def test_i_documented_succeeds_and_transitions() -> None:
    doc_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="claimed", assigned_to=doc_id, team="backend")
    after = MagicMock(**{**t.__dict__, "status": "awaiting_pm_review"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.docs_complete.return_value = after
    task_svc.cell_pm_for_team.return_value = MagicMock(id=uuid4())
    a2a_svc = AsyncMock()
    deps = _make_deps(task=task_svc, a2a=a2a_svc)
    c = Choreographer(deps)

    notes = "Wrote backend/guides/feature-x.md with usage examples and config notes."
    files = ["backend/guides/feature-x.md"]
    env = await c.i_documented(doc_id, task_id, notes=notes, files=files)
    assert env.error is None
    assert env.status == "awaiting_pm_review"
    task_svc.docs_complete.assert_awaited_once()
    a2a_svc.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_documented_not_assigned_returns_not_authorized() -> None:
    doc_id = uuid4()
    other = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="claimed", assigned_to=other)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_documented(doc_id, task_id, notes="x" * 50, files=["x.md"])
    assert env.as_dict()["error"] == "not_authorized"

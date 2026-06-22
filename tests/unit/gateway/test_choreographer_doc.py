"""Tests for Documenter Choreographer methods."""

from __future__ import annotations

from datetime import UTC, datetime
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
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
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
    git_svc = AsyncMock()
    git_svc.diff.return_value = "+++ diff"
    git_svc.list_changed_files.return_value = ["README.md"]
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
    t = MagicMock(
        id=task_id,
        status="in_progress",
        task_type="code",
        team="backend",
        quick_context=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=doc_id, role="documenter", team="backend", slug=None
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    # Spec rejects: in_progress is not in `claim` action's source_statuses
    # (PENDING, NEEDS_REVISION, AWAITING_QA, AWAITING_DOCUMENTATION).
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


def _doc_owned_task(task_id: Any, doc_id: Any, **overrides: Any) -> MagicMock:
    """Build a doc-owned awaiting_documentation task fixture for the spec gate.

    Status defaults to awaiting_documentation (which matches docs_complete's
    spec source_statuses). task_type / team / quick_context defaulted so
    the spec gate evaluates against real values.
    """
    base = {
        "id": task_id,
        "status": "awaiting_documentation",
        "task_type": "code",
        "team": "backend",
        "assigned_to": doc_id,
        "quick_context": None,
    }
    base.update(overrides)
    return MagicMock(**base)


def _doc_agent_mock(doc_id: Any) -> MagicMock:
    return MagicMock(id=doc_id, role="documenter", team="backend", slug=None)


@pytest.mark.asyncio
async def test_i_documented_requires_min_notes() -> None:
    doc_id = uuid4()
    task_id = uuid4()
    t = _doc_owned_task(task_id, doc_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _doc_agent_mock(doc_id)
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_documented(doc_id, task_id, notes="wrote the docs", files=["a.md"])
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "docs_notes>=min" in body["missing"]


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
    t = _doc_owned_task(task_id, doc_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _doc_agent_mock(doc_id)
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    notes = "Wrote backend/guides/feature-x.md with usage examples."
    env = await c.i_documented(doc_id, task_id, notes=notes, files=[])
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "docs_files_non_empty" in body["missing"]


@pytest.mark.asyncio
async def test_i_documented_succeeds_and_transitions() -> None:
    doc_id = uuid4()
    task_id = uuid4()
    t = _doc_owned_task(task_id, doc_id)
    after = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=doc_id,
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _doc_agent_mock(doc_id)
    task_svc.docs_complete.return_value = after
    task_svc.cell_pm_for_team.return_value = MagicMock(id=uuid4())
    task_svc.session = MagicMock()
    task_svc.session.flush = AsyncMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    a2a_svc = AsyncMock()
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, a2a=a2a_svc, journal=journal_svc)
    c = Choreographer(deps)

    notes = "Wrote backend/guides/feature-x.md with usage examples and config notes."
    files = ["backend/guides/feature-x.md"]
    env = await c.i_documented(doc_id, task_id, notes=notes, files=files)
    assert env.error is None
    assert env.status == "awaiting_pm_review"
    task_svc.docs_complete.assert_awaited_once()
    a2a_svc.send.assert_awaited_once()


def _doc_success_task_svc(task_id: Any, doc_id: Any) -> AsyncMock:
    """A task service stubbed for a passing i_documented (awaiting_pm_review)."""
    t = _doc_owned_task(task_id, doc_id)
    after = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=doc_id,
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _doc_agent_mock(doc_id)
    task_svc.docs_complete.return_value = after
    task_svc.cell_pm_for_team.return_value = MagicMock(id=uuid4())
    task_svc.session = MagicMock()
    task_svc.session.flush = AsyncMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return task_svc


@pytest.mark.asyncio
async def test_i_documented_pushes_doc_commit() -> None:
    """The documenter's doc commit must be PUSHED so it reaches the open PR.

    The PR is already open by the time docs are written; without a push the
    doc commit lives only in the documenter's clone and the PM merges a PR
    that excludes the docs. Mirrors the developer's _ensure_branch_pushed.
    """
    doc_id = uuid4()
    task_id = uuid4()
    task_svc = _doc_success_task_svc(task_id, doc_id)
    git_svc = AsyncMock()
    git_svc.push_task_branch.return_value = 1
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    notes = "Wrote backend/guides/feature-x.md with usage examples and config."
    env = await c.i_documented(
        doc_id, task_id, notes=notes, files=["backend/guides/feature-x.md"]
    )
    assert env.error is None
    assert env.status == "awaiting_pm_review"
    git_svc.push_task_branch.assert_awaited_once_with(doc_id, task_id)


@pytest.mark.asyncio
async def test_i_documented_push_failure_holds_task() -> None:
    """A push failure must NOT transition — the docs are local-only, so the
    documenter stays in awaiting_documentation and retries (no silent drop)."""
    doc_id = uuid4()
    task_id = uuid4()
    task_svc = _doc_success_task_svc(task_id, doc_id)
    git_svc = AsyncMock()
    git_svc.push_task_branch.side_effect = RuntimeError("push rejected")
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    notes = "Wrote backend/guides/feature-x.md with usage examples and config."
    env = await c.i_documented(
        doc_id, task_id, notes=notes, files=["backend/guides/feature-x.md"]
    )
    assert env.as_dict()["error"] == "invalid_state"
    task_svc.docs_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_i_documented_not_assigned_returns_not_authorized() -> None:
    doc_id = uuid4()
    other = uuid4()
    task_id = uuid4()
    t = _doc_owned_task(task_id, other)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _doc_agent_mock(doc_id)
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_documented(doc_id, task_id, notes="x" * 50, files=["x.md"])
    assert env.as_dict()["error"] == "not_authorized"

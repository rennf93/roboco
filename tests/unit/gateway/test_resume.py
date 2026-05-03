"""resume transitions a paused task back to in_progress for the assignee.

Audit J33 — `paused -> in_progress` is a valid lifecycle transition but no
agent-callable verb implemented it. `i_will_work_on` only handles
needs_revision/pending/claimed; the closure dispatcher pauses owned
in_progress work on `i_am_idle` but nothing wakes it back up. `resume` fills
that gap. These tests pin the four behaviors:

- happy path: paused -> in_progress for the assignee
- not_found: unknown task id returns the not_found envelope
- not_authorized: only the current claimant can resume
- invalid_state: only paused tasks can be resumed
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    """Local dep-builder. Established pattern: per-test-file, not centralized."""
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
    repo = base["evidence_repo"]
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
    ):
        getattr(repo, method).return_value = []
    return ChoreographerDeps(**base)


@pytest.mark.asyncio
async def test_resume_transitions_paused_to_in_progress() -> None:
    aid = uuid4()
    tid = uuid4()
    t = MagicMock(id=tid, status="paused", assigned_to=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.resume_for_agent.return_value = MagicMock(
        id=tid, status="in_progress", assigned_to=aid
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.resume(aid, tid)

    assert env.error is None
    task_svc.resume_for_agent.assert_awaited_once_with(tid, aid)
    assert env.status == "in_progress"
    assert env.task_id == str(tid)


@pytest.mark.asyncio
async def test_resume_returns_not_found_for_unknown_task() -> None:
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.resume(aid, tid)

    assert env.error == "not_found"
    task_svc.resume_for_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_rejects_when_not_claimant() -> None:
    aid = uuid4()
    other = uuid4()
    tid = uuid4()
    t = MagicMock(id=tid, status="paused", assigned_to=other)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.resume(aid, tid)

    assert env.error == "not_authorized"
    task_svc.resume_for_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_rejects_invalid_state() -> None:
    aid = uuid4()
    tid = uuid4()
    # Task is owned but not paused (e.g. status drifted to in_progress between
    # get and write). Service-level guard refuses by returning None.
    t = MagicMock(id=tid, status="in_progress", assigned_to=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.resume_for_agent.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.resume(aid, tid)

    assert env.error == "invalid_state"
    task_svc.resume_for_agent.assert_awaited_once_with(tid, aid)


@pytest.mark.asyncio
async def test_resume_rejection_writes_audit_row() -> None:
    """Every rejection envelope must call audit.log_event (Task 6 contract)."""
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    audit_svc = AsyncMock()
    deps = _make_deps(task=task_svc, audit=audit_svc)
    c = Choreographer(deps)

    env = await c.resume(aid, tid)

    assert env.error == "not_found"
    audit_svc.log_event.assert_awaited_once()
    kwargs = audit_svc.log_event.await_args.kwargs
    assert kwargs["event_type"] == "gateway.rejected"
    assert kwargs["details"]["verb"] == "resume"


@pytest.mark.asyncio
async def test_resume_success_writes_heartbeat() -> None:
    """Heartbeat fires on success — agent is back to active work."""
    aid = uuid4()
    tid = uuid4()
    t = MagicMock(id=tid, status="paused", assigned_to=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.resume_for_agent.return_value = MagicMock(
        id=tid, status="in_progress", assigned_to=aid
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.resume(aid, tid)

    assert env.error is None
    task_svc.heartbeat.assert_awaited_once_with(tid)

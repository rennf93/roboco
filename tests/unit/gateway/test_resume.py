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

from datetime import UTC, datetime
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
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


@pytest.mark.asyncio
async def test_resume_transitions_paused_to_in_progress() -> None:
    aid = uuid4()
    tid = uuid4()
    t = MagicMock(id=tid, status="paused", assigned_to=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=aid, role="developer", team="backend", slug=None
    )
    task_svc.resume_for_agent.return_value = MagicMock(
        id=tid, status="in_progress", assigned_to=aid
    )
    # VerbRunner wraps composed actions in session.begin_nested(); wire up
    # an async-context-manager mock so the runner doesn't fail on dispatch.
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
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
    task_svc.agent_for.return_value = MagicMock(
        id=aid, role="developer", team="backend", slug=None
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.resume(aid, tid)

    # The spec gate accepts (developer is in resume's allowed_roles and
    # status is paused); the reassignment-rejection branch (Task 6 fix in
    # commit a5d358d) is what rejects with "current owner".
    assert env.error == "not_authorized"
    assert "current owner" in (env.message or "")
    task_svc.resume_for_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_rejects_invalid_state() -> None:
    aid = uuid4()
    tid = uuid4()
    # Task is owned but not paused. resume's IntentSpec composes=("resume",)
    # and the resume ActionSpec's source_statuses is {PAUSED}, so the spec
    # gate rejects with invalid_state — VerbRunner never runs.
    t = MagicMock(id=tid, status="in_progress", assigned_to=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=aid, role="developer", team="backend", slug=None
    )
    task_svc.resume_for_agent.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.resume(aid, tid)

    assert env.error == "invalid_state"
    # Spec gate rejects before the runner dispatches, so resume_for_agent
    # is NOT called.
    task_svc.resume_for_agent.assert_not_awaited()


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
    task_svc.agent_for.return_value = MagicMock(
        id=aid, role="developer", team="backend", slug=None
    )
    task_svc.resume_for_agent.return_value = MagicMock(
        id=tid, status="in_progress", assigned_to=aid
    )
    # VerbRunner wraps composed actions in session.begin_nested(); wire up
    # an async-context-manager mock so the runner doesn't fail on dispatch.
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.resume(aid, tid)

    assert env.error is None
    task_svc.heartbeat.assert_awaited_once_with(tid)

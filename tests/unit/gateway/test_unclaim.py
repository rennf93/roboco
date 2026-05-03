"""unclaim returns claimed/in_progress task to pending and clears assigned_to.

Audit J33 — `Choreographer._pending_assignment_guard` remediate string says
"or unclaim it first" pointing to a verb that did not exist. The verb now
exists. These tests pin the four behaviors:

- happy path: claimed -> pending, assigned_to cleared
- not_found: unknown task id returns the not_found envelope
- not_authorized: only the current claimant can unclaim
- invalid_state: only claimed/in_progress tasks can be unclaimed
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
async def test_unclaim_returns_task_to_pending() -> None:
    aid = uuid4()
    tid = uuid4()
    t = MagicMock(id=tid, status="claimed", assigned_to=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unclaim_for_agent.return_value = MagicMock(
        id=tid, status="pending", assigned_to=None
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.unclaim(aid, tid)

    assert env.error is None
    task_svc.unclaim_for_agent.assert_awaited_once_with(tid, aid)
    assert env.status == "pending"
    assert env.task_id == str(tid)


@pytest.mark.asyncio
async def test_unclaim_returns_not_found_for_unknown_task() -> None:
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.unclaim(aid, tid)

    assert env.error == "not_found"
    task_svc.unclaim_for_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_unclaim_rejects_when_not_claimant() -> None:
    aid = uuid4()
    other = uuid4()
    tid = uuid4()
    t = MagicMock(id=tid, status="claimed", assigned_to=other)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.unclaim(aid, tid)

    assert env.error == "not_authorized"
    task_svc.unclaim_for_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_unclaim_rejects_invalid_state() -> None:
    aid = uuid4()
    tid = uuid4()
    # Status is claimed (assigned_to matches), but the service-level guard
    # refuses (e.g. status drifted to verifying between get and write).
    t = MagicMock(id=tid, status="verifying", assigned_to=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unclaim_for_agent.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.unclaim(aid, tid)

    assert env.error == "invalid_state"
    task_svc.unclaim_for_agent.assert_awaited_once_with(tid, aid)


@pytest.mark.asyncio
async def test_unclaim_rejection_writes_audit_row() -> None:
    """Every rejection envelope must call audit.log_event (Task 6 contract)."""
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    audit_svc = AsyncMock()
    deps = _make_deps(task=task_svc, audit=audit_svc)
    c = Choreographer(deps)

    env = await c.unclaim(aid, tid)

    assert env.error == "not_found"
    audit_svc.log_event.assert_awaited_once()
    kwargs = audit_svc.log_event.await_args.kwargs
    assert kwargs["event_type"] == "gateway.rejected"
    assert kwargs["details"]["verb"] == "unclaim"

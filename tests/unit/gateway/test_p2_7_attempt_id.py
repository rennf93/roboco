"""P2-7: every gateway.rejected audit row carries an attempt_id.

The attempt_id (uuid4 per rejection) lets post-mortem queries group
all attempts on a task within a window, even when multiple calls share
a correlation_id from a single inbound request.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


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


def _is_uuid(s: str) -> bool:
    try:
        UUID(s)
    except (ValueError, TypeError):
        return False
    return True


@pytest.mark.asyncio
async def test_rejection_includes_attempt_id() -> None:
    """A `not_found` rejection on i_am_done emits an audit row with attempt_id."""
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None  # → not_found
    audit_svc = AsyncMock()
    deps = _make_deps(task=task_svc, audit=audit_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(aid, tid, notes="x")

    assert env.error == "not_found"
    audit_svc.log_event.assert_awaited()
    args = audit_svc.log_event.await_args
    details = args.kwargs["details"]
    assert "attempt_id" in details, "P2-7: audit row must include attempt_id"
    assert _is_uuid(details["attempt_id"]), "P2-7: attempt_id must be a UUID string"


@pytest.mark.asyncio
async def test_distinct_rejections_emit_distinct_attempt_ids() -> None:
    """Two rejections in sequence get different attempt_ids."""
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    audit_svc = AsyncMock()
    deps = _make_deps(task=task_svc, audit=audit_svc)
    c = Choreographer(deps)

    await c.i_am_done(aid, tid, notes="x")
    await c.i_am_done(aid, tid, notes="x")

    expected_distinct_ids = 2
    calls = audit_svc.log_event.await_args_list
    ids = {call.kwargs["details"]["attempt_id"] for call in calls}
    assert len(ids) == expected_distinct_ids, (
        "P2-7: each rejection emits its own attempt_id"
    )


@pytest.mark.asyncio
async def test_success_envelope_does_not_emit_audit() -> None:
    """Confirms the contract: audit rows fire on rejection only.

    attempt_id machinery doesn't trip on success (no row to stamp).
    """
    aid = uuid4()
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    audit_svc = AsyncMock()
    deps = _make_deps(task=task_svc, audit=audit_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(aid)

    assert env.error is None
    audit_svc.log_event.assert_not_awaited()

"""Every Envelope rejection from a Choreographer verb writes an audit row.

Choreographer takes an ``audit`` dependency but historically never invoked
it. The result: every rejection envelope (invalid_state, not_authorized,
tracing_gap, not_found) silently disappeared. With no forensic trail, a
stuck flow had no breadcrumbs.

These tests pin the ``gateway.rejected`` audit-write behavior across the
range of rejection-returning verbs, including:

- not_authorized rejections (PM cannot execute code, role-typed claim)
- invalid_state rejections (no active task, expected status mismatch)
- tracing_gap rejections (missing notes, missing journal entries)
- not_found rejections (unknown task id)

The audit call is fire-and-forget; an exception inside ``log_event`` must
not propagate or alter the envelope returned to the agent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

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
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


# ---------------------------------------------------------------------------
# Primary acceptance test: PM cannot claim a code task — not_authorized path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_cannot_execute_code_writes_audit_row() -> None:
    """A cell_pm calling i_will_work_on on a code task must:

    1. Return an Envelope with error == 'not_authorized'
    2. Write a gateway.rejected audit event with verb + reason details
    """
    aid = uuid4()
    tid = uuid4()
    code_task = MagicMock(
        id=tid,
        status="pending",
        assigned_to=aid,
        task_type="code",
        priority=1,
        parent_task_id=None,
        sequence=0,
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = code_task
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    audit_svc = AsyncMock()
    deps = _make_deps(task=task_svc, audit=audit_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(aid, tid, plan="x")

    assert env.error == "not_authorized"
    audit_svc.log_event.assert_awaited()
    args = audit_svc.log_event.await_args
    assert args.kwargs["event_type"] == "gateway.rejected"
    assert args.kwargs["details"]["verb"] == "i_will_work_on"
    assert args.kwargs["details"]["reason"] == "not_authorized"


# ---------------------------------------------------------------------------
# not_found path: unknown task id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_task_writes_audit_row() -> None:
    """not_found rejection (unknown task id) is audited."""
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    audit_svc = AsyncMock()
    deps = _make_deps(task=task_svc, audit=audit_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(aid, tid, notes="something")

    assert env.error == "not_found"
    audit_svc.log_event.assert_awaited()
    args = audit_svc.log_event.await_args
    assert args.kwargs["event_type"] == "gateway.rejected"
    assert args.kwargs["details"]["verb"] == "i_am_done"
    assert args.kwargs["details"]["reason"] == "not_found"


# ---------------------------------------------------------------------------
# Happy path must NOT write an audit row.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_verb_does_not_write_audit_row() -> None:
    """Successful (non-error) Envelope must not emit gateway.rejected audit."""
    aid = uuid4()
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    audit_svc = AsyncMock()
    deps = _make_deps(task=task_svc, audit=audit_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(aid)

    # No rejection, so no audit row.
    assert env.error is None
    audit_svc.log_event.assert_not_awaited()


# ---------------------------------------------------------------------------
# Audit failure must NOT block the verb (best-effort rule).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_event_failure_does_not_propagate() -> None:
    """If log_event raises, the verb still returns the rejection envelope."""
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    # Unknown task id triggers not_found rejection on i_am_done.
    task_svc.get.return_value = None
    audit_svc = AsyncMock()
    audit_svc.log_event.side_effect = RuntimeError("audit DB down")
    deps = _make_deps(task=task_svc, audit=audit_svc)
    c = Choreographer(deps)

    # Must not raise; the rejection envelope should still come back.
    env = await c.i_am_done(aid, tid, notes="x")

    assert env.error == "not_found"
    audit_svc.log_event.assert_awaited()

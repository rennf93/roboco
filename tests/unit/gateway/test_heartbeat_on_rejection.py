"""Wave C3 (2026-05-12): _emit_rejection must touch last_heartbeat_at.

Pre-fix: heartbeat only refreshed on verb SUCCESS.  Smoke run 3 showed
agents being reaped while actively retrying rejected verbs — the
heartbeat was stale even though the agent was alive and calling verbs.

Fix: _emit_rejection calls self._touch(task_id) for every rejection so
the agent's heartbeat stays current even in a pure-rejection loop.

Constraints (from spec):
  - The touch must NOT fire on success envelopes (_emit_rejection already
    short-circuits on env.error is None — this is unchanged).
  - The touch is best-effort: a task.heartbeat() failure must not change
    the envelope returned to the agent.
  - task_id=None is safe: _touch already guards that case.
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
    task = base["task"]
    task.session = MagicMock()
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


# ---------------------------------------------------------------------------
# Core acceptance: rejection on a role-mismatch fires heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_fires_on_rejection_not_authorized() -> None:
    """A not_authorized rejection (PM trying code task) must still touch heartbeat."""
    aid = uuid4()
    tid = uuid4()
    code_task = MagicMock(
        id=tid,
        status="pending",
        assigned_to=None,
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
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(aid, tid, plan="x")

    assert env.error == "not_authorized"
    task_svc.heartbeat.assert_awaited_with(tid)


# ---------------------------------------------------------------------------
# not_found rejection also touches heartbeat (task_id still known from arg)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_fires_on_not_found_rejection() -> None:
    """not_found rejection passes task_id to _emit_rejection; heartbeat still fires."""
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None  # task not found
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(aid, tid, notes="something")

    assert env.error == "not_found"
    task_svc.heartbeat.assert_awaited_with(tid)


# ---------------------------------------------------------------------------
# Success path must NOT get an extra heartbeat from _emit_rejection
# (the existing _touch calls on the success path already cover it)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_not_double_fired_on_success() -> None:
    """give_me_work idle-path succeeds without touching heartbeat at all."""
    aid = uuid4()
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(aid)

    assert env.error is None
    task_svc.heartbeat.assert_not_awaited()


# ---------------------------------------------------------------------------
# Heartbeat failure on rejection must not swallow or alter the envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_failure_on_rejection_does_not_propagate() -> None:
    """If task.heartbeat() raises during a rejection, the envelope is still returned."""
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    task_svc.heartbeat.side_effect = RuntimeError("DB down")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(aid, tid, notes="x")

    assert env.error == "not_found"

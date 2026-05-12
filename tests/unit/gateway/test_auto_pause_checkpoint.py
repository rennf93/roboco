"""Wave C7 (2026-05-12): auto-pause on i_am_idle writes a synthetic checkpoint.

Smoke run 3 showed agents auto-pausing on i_am_idle (correct behavior for
non-terminal tasks) but capturing no checkpoint — panel's Checkpoints column
stayed empty. Pre-gateway parity: the auto-pause path now writes a synthetic
checkpoint summarizing state at pause-time so the panel reflects reality.
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


@pytest.mark.asyncio
async def test_i_am_idle_with_in_progress_task_writes_checkpoint() -> None:
    """When i_am_idle auto-pauses an in_progress task, a synthetic checkpoint is
    written with the correct task_id, agent_id, and a summary mentioning auto-pause.
    """
    agent_id = uuid4()
    task_id = uuid4()

    task_obj = MagicMock()
    task_obj.id = task_id
    task_obj.status = "in_progress"
    task_obj.assigned_to = agent_id
    task_obj.commits = []

    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_in_progress_for_agent.return_value = [task_obj]
    task_svc.pause_for_agent = AsyncMock()
    task_svc.add_checkpoint = AsyncMock()

    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    body = env.as_dict()

    assert body["error"] is None
    assert body["status"] == "idle"

    task_svc.add_checkpoint.assert_awaited_once()
    call_kwargs = task_svc.add_checkpoint.await_args
    assert call_kwargs is not None
    # task_id and agent_id must be present
    assert call_kwargs.kwargs.get("task_id") == task_id or (
        len(call_kwargs.args) >= 1 and call_kwargs.args[0] == task_id
    )
    second_arg_index = 1
    assert call_kwargs.kwargs.get("agent_id") == agent_id or (
        len(call_kwargs.args) > second_arg_index
        and call_kwargs.args[second_arg_index] == agent_id
    )
    # Summary must mention auto-pause
    state_summary = call_kwargs.kwargs.get("state_summary", "")
    assert "auto-pause" in state_summary or "auto_pause" in state_summary


@pytest.mark.asyncio
async def test_i_am_idle_multiple_in_progress_tasks_each_get_checkpoint() -> None:
    """Each auto-paused task gets its own synthetic checkpoint."""
    agent_id = uuid4()
    task_id_1 = uuid4()
    task_id_2 = uuid4()

    commit_a = MagicMock()
    commit_a.sha = "aaa111"
    commit_b = MagicMock()
    commit_b.sha = "bbb222"

    task_1 = MagicMock()
    task_1.id = task_id_1
    task_1.status = "in_progress"
    task_1.commits = [commit_a, commit_b]

    task_2 = MagicMock()
    task_2.id = task_id_2
    task_2.status = "in_progress"
    task_2.commits = []

    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_in_progress_for_agent.return_value = [task_1, task_2]
    task_svc.pause_for_agent = AsyncMock()
    task_svc.add_checkpoint = AsyncMock()

    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    await c.i_am_idle(agent_id)

    expected_checkpoint_count = 2
    assert task_svc.add_checkpoint.await_count == expected_checkpoint_count
    called_task_ids = {
        kw.kwargs.get("task_id") or kw.args[0]
        for kw in task_svc.add_checkpoint.await_args_list
    }
    assert task_id_1 in called_task_ids
    assert task_id_2 in called_task_ids


@pytest.mark.asyncio
async def test_i_am_idle_with_commits_includes_last_three_in_remaining_work() -> None:
    """Checkpoint's remaining_work contains refs for the last 3 commits."""
    agent_id = uuid4()
    task_id = uuid4()

    commits = [MagicMock(sha=f"sha{i}") for i in range(5)]
    task_obj = MagicMock()
    task_obj.id = task_id
    task_obj.status = "in_progress"
    task_obj.commits = commits

    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_in_progress_for_agent.return_value = [task_obj]
    task_svc.pause_for_agent = AsyncMock()
    task_svc.add_checkpoint = AsyncMock()

    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    await c.i_am_idle(agent_id)

    call_kwargs = task_svc.add_checkpoint.await_args
    remaining = call_kwargs.kwargs.get("remaining_work", [])
    # Last 3 commit SHAs should appear somewhere in remaining_work entries
    last_3_shas = {c.sha for c in commits[-3:]}
    mentioned_shas = {entry for entry in remaining if isinstance(entry, str)}
    assert last_3_shas & mentioned_shas or any(
        sha in str(remaining) for sha in last_3_shas
    )


@pytest.mark.asyncio
async def test_i_am_idle_checkpoint_failure_does_not_block_auto_pause() -> None:
    """If add_checkpoint raises, the auto-pause and idle response still succeed."""
    agent_id = uuid4()
    task_id = uuid4()

    task_obj = MagicMock()
    task_obj.id = task_id
    task_obj.status = "in_progress"
    task_obj.commits = []

    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_in_progress_for_agent.return_value = [task_obj]
    task_svc.pause_for_agent = AsyncMock()
    task_svc.add_checkpoint = AsyncMock(side_effect=RuntimeError("DB timeout"))

    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    body = env.as_dict()

    # The idle response must still succeed even though checkpoint write failed
    assert body["error"] is None
    assert body["status"] == "idle"
    # The pause must still have happened
    task_svc.pause_for_agent.assert_awaited_once_with(agent_id, task_id)
    task_svc.mark_agent_idle.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_am_idle_with_no_active_task_skips_checkpoint() -> None:
    """No active in_progress task → no auto-pause, no checkpoint written."""
    agent_id = uuid4()

    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.add_checkpoint = AsyncMock()

    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_idle(agent_id)
    body = env.as_dict()

    assert body["error"] is None
    assert body["status"] == "idle"
    task_svc.add_checkpoint.assert_not_awaited()

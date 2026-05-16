"""#170: a rejected PM must be told the exact complete() call to make.

Smoke-15 wedge: leaf 1533ce56 sat at awaiting_pm_review owned by
be-pm, but be-pm/main-pm looped firing complete/unblock at the wrong
(parent) task_ids — no rejection ever named the one actionable task,
so minimax never issued `complete(1533ce56)`. The complete guards now
append `_own_review_hint`: if the PM owns a DIFFERENT task that is
awaiting_pm_review, the remediate names it + the exact call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
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
        "messaging": AsyncMock(),
    }
    base.update(overrides)
    repo = base["evidence_repo"]
    for m in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, m).return_value = []
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _owned(task_id: Any, status: str) -> SimpleNamespace:
    return SimpleNamespace(id=task_id, status=status)


@pytest.mark.asyncio
async def test_hint_names_the_owned_awaiting_review_task() -> None:
    pm = uuid4()
    ready_id = uuid4()
    rejected_id = uuid4()
    deps = _make_deps()
    deps.task.list_by_assignee = AsyncMock(
        return_value=[
            _owned(rejected_id, "in_progress"),
            _owned(ready_id, "awaiting_pm_review"),
        ]
    )
    c = Choreographer(deps)

    hint = await c._own_review_hint(pm, rejected_id)
    assert str(ready_id) in hint
    assert f"complete(task_id='{ready_id}'" in hint


@pytest.mark.asyncio
async def test_hint_excludes_the_rejected_task_itself() -> None:
    pm = uuid4()
    same = uuid4()
    deps = _make_deps()
    deps.task.list_by_assignee = AsyncMock(
        return_value=[_owned(same, "awaiting_pm_review")]
    )
    c = Choreographer(deps)
    assert await c._own_review_hint(pm, same) == ""


@pytest.mark.asyncio
async def test_hint_empty_when_nothing_ready() -> None:
    pm = uuid4()
    deps = _make_deps()
    deps.task.list_by_assignee = AsyncMock(
        return_value=[_owned(uuid4(), "in_progress")]
    )
    c = Choreographer(deps)
    assert await c._own_review_hint(pm, uuid4()) == ""


@pytest.mark.asyncio
async def test_hint_best_effort_swallows_errors() -> None:
    pm = uuid4()
    deps = _make_deps()
    deps.task.list_by_assignee = AsyncMock(side_effect=RuntimeError("db down"))
    c = Choreographer(deps)
    assert await c._own_review_hint(pm, uuid4()) == ""


@pytest.mark.asyncio
async def test_cell_pm_complete_guard_not_owner_surfaces_hint() -> None:
    """The smoke-15 case: main-pm calls complete on a leaf it doesn't
    own while its real task is awaiting_pm_review elsewhere."""
    caller = uuid4()
    other_owner = uuid4()
    rejected_id = uuid4()
    ready_id = uuid4()
    deps = _make_deps()
    deps.task.list_by_assignee = AsyncMock(
        return_value=[_owned(ready_id, "awaiting_pm_review")]
    )
    c = Choreographer(deps)
    t = MagicMock(
        id=rejected_id,
        assigned_to=other_owner,
        status="awaiting_pm_review",
        team="backend",
        task_type="code",
    )

    env = await c._cell_pm_complete_guard(caller, rejected_id, t, "notes")
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert f"complete(task_id='{ready_id}'" in body["remediate"]


@pytest.mark.asyncio
async def test_cell_pm_complete_guard_wrong_state_surfaces_hint() -> None:
    pm = uuid4()
    rejected_id = uuid4()
    ready_id = uuid4()
    deps = _make_deps()
    deps.task.list_by_assignee = AsyncMock(
        return_value=[_owned(ready_id, "awaiting_pm_review")]
    )
    c = Choreographer(deps)
    t = MagicMock(
        id=rejected_id,
        assigned_to=pm,
        status="in_progress",
        team="backend",
        task_type="planning",
    )

    env = await c._cell_pm_complete_guard(pm, rejected_id, t, "notes")
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert f"complete(task_id='{ready_id}'" in body["remediate"]

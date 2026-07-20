"""The block/unblock flip-flop breaker.

Live wedge: fe-pm's escalate_up auto-blocks a task and main_pm's unblock
resolves it, repeat — 10 flips, 43 spawns, no forward progress, no cycle
breaker. ``unblock`` now stamps a per-task flip counter
(``markers.block_flip_count``) and, at exactly the 3rd flip, best-effort
alerts the CEO once — the unblock itself always still succeeds.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.notification import NotificationService

# Named constants — ruff PLR2004 forbids magic-value comparisons.
_TWO_FLIPS = 2
_THREE_FLIPS = 3
_FOUR_FLIPS = 4


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
    base["journal"].has_decision_for_task.return_value = True
    base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _flip_setup() -> tuple[Choreographer, Any, Any, Any]:
    """A blocked task whose ``unblock_with_restore`` returns the SAME mock
    object each call, so the flip-counter marker persists across repeated
    unblock() calls the way it would on one real ORM row across requests.
    """
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="blocked",
        pre_block_state="in_progress",
        pre_block_assignee=uuid4(),
        pre_block_metadata={},
        dependency_ids=[],
        orchestration_markers=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unblock_with_restore.return_value = t
    task_svc.unmet_dependency_ids.return_value = []
    c = Choreographer(_make_deps(task=task_svc))
    return c, pm_id, task_id, t


async def _unblock_once(c: Choreographer, pm_id: Any, task_id: Any, t: Any) -> Any:
    """Re-block before each call — a fresh flip in the cycle."""
    t.status = "blocked"
    return await c.unblock(pm_id, task_id, "resolved upstream; restoring")


@pytest.mark.asyncio
async def test_first_and_second_unblock_do_not_notify() -> None:
    c, pm_id, task_id, t = _flip_setup()
    cc: Any = c
    notify = AsyncMock()
    cc._notify_ceo_block_flip = notify

    for _ in range(2):
        env = await _unblock_once(c, pm_id, task_id, t)
        assert env.error is None, env.as_dict()

    notify.assert_not_awaited()
    assert markers.get_block_flip_count(t) == _TWO_FLIPS


@pytest.mark.asyncio
async def test_third_unblock_notifies_ceo_once() -> None:
    c, pm_id, task_id, t = _flip_setup()
    cc: Any = c
    notify = AsyncMock()
    cc._notify_ceo_block_flip = notify

    for _ in range(3):
        env = await _unblock_once(c, pm_id, task_id, t)
        assert env.error is None, env.as_dict()

    notify.assert_awaited_once_with(task_id, _THREE_FLIPS, t.title)
    assert markers.is_block_flip_notified(t) is True


@pytest.mark.asyncio
async def test_fourth_unblock_does_not_renotify() -> None:
    c, pm_id, task_id, t = _flip_setup()
    cc: Any = c
    notify = AsyncMock()
    cc._notify_ceo_block_flip = notify

    for _ in range(4):
        env = await _unblock_once(c, pm_id, task_id, t)
        assert env.error is None, env.as_dict()

    notify.assert_awaited_once()
    assert markers.get_block_flip_count(t) == _FOUR_FLIPS


@pytest.mark.asyncio
async def test_notification_failure_does_not_fail_unblock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real ``_notify_ceo_block_flip`` swallows a notify-service failure —
    unblock still succeeds on the 3rd flip."""
    c, pm_id, task_id, t = _flip_setup()
    monkeypatch.setattr(
        NotificationService,
        "send_block_flip_notification",
        AsyncMock(side_effect=RuntimeError("notification service down")),
    )

    env = None
    for _ in range(3):
        env = await _unblock_once(c, pm_id, task_id, t)
        assert env.error is None, env.as_dict()

    assert env is not None
    assert env.error is None
    assert markers.is_block_flip_notified(t) is True


@pytest.mark.asyncio
async def test_counter_persists_via_marker_across_calls() -> None:
    c, pm_id, task_id, t = _flip_setup()
    cc: Any = c
    cc._notify_ceo_block_flip = AsyncMock()

    await _unblock_once(c, pm_id, task_id, t)
    assert markers.get_block_flip_count(t) == 1
    await _unblock_once(c, pm_id, task_id, t)
    assert markers.get_block_flip_count(t) == _TWO_FLIPS

"""``already_active_guard`` must treat a ``blocked`` task as active. A blocked
task is still owned and will resume to ``in_progress`` on unblock, so it must
block a new claim (preserves the one-active-task-per-dev invariant).
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from roboco.services.gateway.claim_guards import already_active_guard


def _task(*, status: str) -> MagicMock:
    t = MagicMock()
    t.id = uuid4()
    t.status = status
    return t


def test_already_active_guard_blocks_when_agent_has_blocked_task() -> None:
    """A blocked task the dev still owns must block a new claim."""
    target_id = uuid4()
    blocked = _task(status="blocked")
    env = already_active_guard([blocked], target_id)
    assert env is not None
    assert env.error == "invalid_state"


def test_already_active_guard_still_blocks_in_progress() -> None:
    """Regression: the existing in_progress case still fires."""
    target_id = uuid4()
    in_progress = _task(status="in_progress")
    env = already_active_guard([in_progress], target_id)
    assert env is not None


def test_already_active_guard_excludes_target_task_itself() -> None:
    """Re-claiming/resuming the same blocked task must not self-block."""
    target_id = uuid4()
    blocked_self = MagicMock(id=target_id, status="blocked")
    env = already_active_guard([blocked_self], target_id)
    assert env is None


def test_already_active_guard_passes_when_no_active_tasks() -> None:
    """A dev with only terminal/backlog tasks may claim."""
    target_id = uuid4()
    others = [_task(status="completed"), _task(status="backlog")]
    env = already_active_guard(others, target_id)
    assert env is None

"""Single-claimant invariant + heartbeat staleness detection.

Pure functions. Persistence (writing tasks.active_claimant_id /
last_heartbeat_at) is the caller's responsibility — the choreographer
handles DB writes after consulting these decisions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID


class ClaimDecision(StrEnum):
    GRANTED = "granted"
    GRANTED_AFTER_STALE_RELEASE = "granted_after_stale_release"
    BLOCKED_OTHER_ACTIVE = "blocked_other_active"


def is_stale(task: Any, *, threshold_seconds: int) -> bool:
    """A claim is stale when there is no heartbeat or the heartbeat is older
    than `threshold_seconds`. Tasks with no `active_claimant_id` are not
    'stale' (they have no claim) — callers should check that separately.
    """
    if task.last_heartbeat_at is None:
        return True
    delta: float = (datetime.now(tz=UTC) - task.last_heartbeat_at).total_seconds()
    return delta >= threshold_seconds


def try_acquire(*, task: Any, agent_id: UUID, threshold_seconds: int) -> ClaimDecision:
    """Decide whether `agent_id` may acquire (or refresh) the claim on `task`.

    - GRANTED: no active claimant OR same agent already active (heartbeat refresh).
    - GRANTED_AFTER_STALE_RELEASE: other agent active but their claim is stale.
    - BLOCKED_OTHER_ACTIVE: other agent active and fresh.
    """
    if task.active_claimant_id is None:
        return ClaimDecision.GRANTED
    if task.active_claimant_id == agent_id:
        return ClaimDecision.GRANTED
    if is_stale(task, threshold_seconds=threshold_seconds):
        return ClaimDecision.GRANTED_AFTER_STALE_RELEASE
    return ClaimDecision.BLOCKED_OTHER_ACTIVE

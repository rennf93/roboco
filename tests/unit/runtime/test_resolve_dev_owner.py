"""Owner resolution for dev dispatch — _resolve_dev_owner_uuid.

A stale-claim reap (or a half-applied ownership write) can leave a task
``pending`` with ``assigned_to`` nulled but ``claimed_by`` still set. The dev
dispatcher must still resolve an owner from ``claimed_by`` so the task is
re-spawned instead of going dormant. For ``claimed``/``blocked`` the live
claimant (``claimed_by``) wins; for every other status ``assigned_to`` is the
PM-assigned owner and wins, falling back to ``claimed_by``.
"""

from __future__ import annotations

from typing import Any

from roboco.runtime.orchestrator import AgentOrchestrator

_ASSIGNED = "11111111-1111-1111-1111-111111111111"
_CLAIMED = "22222222-2222-2222-2222-222222222222"


def _resolve(status: str, *, assigned: str | None, claimed: str | None) -> str | None:
    task: dict[str, Any] = {
        "status": status,
        "assigned_to": assigned,
        "claimed_by": claimed,
    }
    return AgentOrchestrator._resolve_dev_owner_uuid(task)


# ---------------------------------------------------------------------------
# pending — assigned_to preferred, claimed_by is the fallback (Bug 3 / 06b0802f)
# ---------------------------------------------------------------------------


def test_pending_prefers_assigned_to() -> None:
    assert _resolve("pending", assigned=_ASSIGNED, claimed=_CLAIMED) == _ASSIGNED


def test_pending_falls_back_to_claimed_by_when_unassigned() -> None:
    # The half-reap case: assigned_to nulled, claimed_by survives.
    assert _resolve("pending", assigned=None, claimed=_CLAIMED) == _CLAIMED


def test_pending_with_no_owner_returns_none() -> None:
    assert _resolve("pending", assigned=None, claimed=None) is None


# ---------------------------------------------------------------------------
# claimed / blocked — the live claimant wins, assigned_to is the fallback
# ---------------------------------------------------------------------------


def test_claimed_prefers_claimed_by() -> None:
    assert _resolve("claimed", assigned=_ASSIGNED, claimed=_CLAIMED) == _CLAIMED


def test_blocked_prefers_claimed_by() -> None:
    assert _resolve("blocked", assigned=_ASSIGNED, claimed=_CLAIMED) == _CLAIMED


def test_blocked_falls_back_to_assigned_to() -> None:
    assert _resolve("blocked", assigned=_ASSIGNED, claimed=None) == _ASSIGNED


# ---------------------------------------------------------------------------
# other statuses — assigned_to preferred, claimed_by fallback
# ---------------------------------------------------------------------------


def test_in_progress_prefers_assigned_to() -> None:
    assert _resolve("in_progress", assigned=_ASSIGNED, claimed=_CLAIMED) == _ASSIGNED


def test_in_progress_falls_back_to_claimed_by() -> None:
    assert _resolve("in_progress", assigned=None, claimed=_CLAIMED) == _CLAIMED


def test_needs_revision_prefers_assigned_to() -> None:
    assert _resolve("needs_revision", assigned=_ASSIGNED, claimed=_CLAIMED) == _ASSIGNED

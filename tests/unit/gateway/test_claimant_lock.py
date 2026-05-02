"""Tests for single-claimant invariant + heartbeat staleness detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from roboco.services.gateway.claimant_lock import (
    ClaimDecision,
    is_stale,
    try_acquire,
)


def _task(
    active_claimant_id: UUID | None = None,
    last_heartbeat_at: datetime | None = None,
) -> MagicMock:
    t = MagicMock()
    t.id = uuid4()
    t.active_claimant_id = active_claimant_id
    t.last_heartbeat_at = last_heartbeat_at
    return t


class TestIsStale:
    def test_no_heartbeat_is_stale(self) -> None:
        t = _task(active_claimant_id=uuid4(), last_heartbeat_at=None)
        assert is_stale(t, threshold_seconds=180) is True

    def test_recent_heartbeat_not_stale(self) -> None:
        recent = datetime.now(tz=UTC) - timedelta(seconds=30)
        t = _task(active_claimant_id=uuid4(), last_heartbeat_at=recent)
        assert is_stale(t, threshold_seconds=180) is False

    def test_old_heartbeat_is_stale(self) -> None:
        old = datetime.now(tz=UTC) - timedelta(seconds=300)
        t = _task(active_claimant_id=uuid4(), last_heartbeat_at=old)
        assert is_stale(t, threshold_seconds=180) is True


class TestTryAcquire:
    def test_acquire_when_no_active_claimant(self) -> None:
        agent = uuid4()
        t = _task(active_claimant_id=None, last_heartbeat_at=None)
        decision = try_acquire(task=t, agent_id=agent, threshold_seconds=180)
        assert decision is ClaimDecision.GRANTED

    def test_acquire_when_same_agent_already_active(self) -> None:
        agent = uuid4()
        recent = datetime.now(tz=UTC)
        t = _task(active_claimant_id=agent, last_heartbeat_at=recent)
        decision = try_acquire(task=t, agent_id=agent, threshold_seconds=180)
        assert decision is ClaimDecision.GRANTED  # heartbeat refresh

    def test_blocked_when_other_agent_active_fresh(self) -> None:
        other = uuid4()
        me = uuid4()
        recent = datetime.now(tz=UTC)
        t = _task(active_claimant_id=other, last_heartbeat_at=recent)
        decision = try_acquire(task=t, agent_id=me, threshold_seconds=180)
        assert decision is ClaimDecision.BLOCKED_OTHER_ACTIVE

    def test_acquire_when_other_agent_stale(self) -> None:
        other = uuid4()
        me = uuid4()
        old = datetime.now(tz=UTC) - timedelta(seconds=600)
        t = _task(active_claimant_id=other, last_heartbeat_at=old)
        decision = try_acquire(task=t, agent_id=me, threshold_seconds=180)
        assert decision is ClaimDecision.GRANTED_AFTER_STALE_RELEASE

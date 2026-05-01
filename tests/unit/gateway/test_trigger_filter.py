"""Tests for stale-trigger cleanup + cooldown decisions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from roboco.services.gateway.trigger_filter import (
    SpawnConfig,
    SpawnDecision,
    TriggerContext,
    TriggerKind,
    decide_spawn,
)

_DEFAULT_CONFIG = SpawnConfig(
    cooldown_seconds=60,
    role_rate_per_minute=6,
    claim_stale_seconds=180,
)


def _task(status: str, active_claimant_id=None, last_heartbeat_at=None):
    t = MagicMock()
    t.id = uuid4()
    t.status = status
    t.active_claimant_id = active_claimant_id
    t.last_heartbeat_at = last_heartbeat_at
    return t


def _trigger(
    kind: TriggerKind,
    skill: str | None = None,
    recent_spawns_for_task: int = 0,
    recent_spawns_for_role: int = 0,
) -> TriggerContext:
    return TriggerContext(
        kind=kind,
        skill=skill,
        recent_spawns_for_task=recent_spawns_for_task,
        recent_spawns_for_role=recent_spawns_for_role,
    )


class TestStaleTriggerCleanup:
    def test_a2a_code_review_for_completed_task_dropped(self) -> None:
        t = _task(status="completed")
        decision = decide_spawn(
            task=t,
            trigger=_trigger(TriggerKind.A2A, skill="code_review"),
            config=_DEFAULT_CONFIG,
        )
        assert decision.outcome == SpawnDecision.DROP
        assert "stale" in decision.reason.lower()

    def test_a2a_code_review_for_awaiting_qa_spawns(self) -> None:
        t = _task(status="awaiting_qa")
        decision = decide_spawn(
            task=t,
            trigger=_trigger(TriggerKind.A2A, skill="code_review"),
            config=_DEFAULT_CONFIG,
        )
        assert decision.outcome == SpawnDecision.SPAWN

    def test_notification_for_terminal_task_dropped(self) -> None:
        t = _task(status="cancelled")
        decision = decide_spawn(
            task=t,
            trigger=_trigger(TriggerKind.NOTIFICATION),
            config=_DEFAULT_CONFIG,
        )
        assert decision.outcome == SpawnDecision.DROP


class TestSingleClaimantQueue:
    def test_active_fresh_claimant_queues(self) -> None:
        recent = datetime.now(tz=UTC)
        t = _task(
            status="in_progress",
            active_claimant_id=uuid4(),
            last_heartbeat_at=recent,
        )
        decision = decide_spawn(
            task=t,
            trigger=_trigger(TriggerKind.NOTIFICATION),
            config=_DEFAULT_CONFIG,
        )
        assert decision.outcome == SpawnDecision.QUEUE
        assert "claimant" in decision.reason.lower()

    def test_stale_claimant_does_not_queue(self) -> None:
        old = datetime.now(tz=UTC) - timedelta(seconds=600)
        t = _task(
            status="awaiting_qa",
            active_claimant_id=uuid4(),
            last_heartbeat_at=old,
        )
        decision = decide_spawn(
            task=t,
            trigger=_trigger(TriggerKind.A2A, skill="code_review"),
            config=_DEFAULT_CONFIG,
        )
        assert decision.outcome == SpawnDecision.SPAWN


class TestCooldown:
    def test_per_task_cooldown_queues(self) -> None:
        t = _task(status="awaiting_qa")
        decision = decide_spawn(
            task=t,
            trigger=_trigger(
                TriggerKind.A2A,
                skill="code_review",
                recent_spawns_for_task=1,
            ),
            config=_DEFAULT_CONFIG,
        )
        assert decision.outcome == SpawnDecision.QUEUE
        assert "cooldown" in decision.reason.lower()

    def test_role_rate_limit_queues(self) -> None:
        t = _task(status="awaiting_qa")
        decision = decide_spawn(
            task=t,
            trigger=_trigger(
                TriggerKind.A2A,
                skill="code_review",
                recent_spawns_for_role=6,
            ),
            config=_DEFAULT_CONFIG,
        )
        assert decision.outcome == SpawnDecision.QUEUE
        assert "rate" in decision.reason.lower()

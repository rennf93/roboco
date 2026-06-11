"""Tests for stale-trigger cleanup + cooldown decisions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import UUID, uuid4

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


def _task(
    status: str,
    active_claimant_id: UUID | None = None,
    last_heartbeat_at: datetime | None = None,
) -> MagicMock:
    t = MagicMock()
    t.id = uuid4()
    t.status = status
    t.active_claimant_id = active_claimant_id
    t.last_heartbeat_at = last_heartbeat_at
    return t


def _trigger(  # noqa: PLR0913
    kind: TriggerKind,
    skill: str | None = None,
    recent_spawns_for_task: int = 0,
    recent_spawns_for_role: int = 0,
    provider: str | None = None,
    provider_rate_limited: bool = False,
) -> TriggerContext:
    return TriggerContext(
        kind=kind,
        skill=skill,
        recent_spawns_for_task=recent_spawns_for_task,
        recent_spawns_for_role=recent_spawns_for_role,
        provider=provider,
        provider_rate_limited=provider_rate_limited,
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

    def test_a2a_code_review_for_paused_task_dropped(self) -> None:
        """Line 79-82: non-relevant non-terminal status (paused) → drop."""
        t = _task(status="paused")
        decision = decide_spawn(
            task=t,
            trigger=_trigger(TriggerKind.A2A, skill="code_review"),
            config=_DEFAULT_CONFIG,
        )
        assert decision.outcome == SpawnDecision.DROP
        assert "code_review" in decision.reason

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


class TestProviderRateLimitGate:
    """Rule 2: provider rate-limit gate fires before claimant-lock/cooldown."""

    def test_queues_when_provider_rate_limited(self) -> None:
        """QUEUE outcome when provider_rate_limited=True."""
        t = _task(status="in_progress")
        decision = decide_spawn(
            task=t,
            trigger=_trigger(
                TriggerKind.NOTIFICATION,
                provider="anthropic",
                provider_rate_limited=True,
            ),
            config=_DEFAULT_CONFIG,
        )
        assert decision.outcome == SpawnDecision.QUEUE
        assert "provider anthropic rate-limited" in decision.reason

    def test_reason_contains_provider_name(self) -> None:
        """Reason string must contain the provider name."""
        t = _task(status="pending")
        decision = decide_spawn(
            task=t,
            trigger=_trigger(
                TriggerKind.SCAN,
                provider="ollama_cloud",
                provider_rate_limited=True,
            ),
            config=_DEFAULT_CONFIG,
        )
        assert "ollama_cloud" in decision.reason

    def test_reason_contains_unknown_when_no_provider_name(self) -> None:
        """When provider is None, reason still contains 'unknown'."""
        t = _task(status="in_progress")
        decision = decide_spawn(
            task=t,
            trigger=_trigger(
                TriggerKind.NOTIFICATION,
                provider=None,
                provider_rate_limited=True,
            ),
            config=_DEFAULT_CONFIG,
        )
        assert decision.outcome == SpawnDecision.QUEUE
        assert "unknown" in decision.reason

    def test_no_queue_injection_when_not_rate_limited(self) -> None:
        """SPAWN when provider_rate_limited=False and all other gates clear."""
        t = _task(status="in_progress")
        decision = decide_spawn(
            task=t,
            trigger=_trigger(
                TriggerKind.NOTIFICATION,
                provider="anthropic",
                provider_rate_limited=False,
            ),
            config=_DEFAULT_CONFIG,
        )
        assert decision.outcome == SpawnDecision.SPAWN

    def test_stale_drop_fires_before_rate_limit_gate(self) -> None:
        """Rule 1 (stale-drop) fires before rule 2 (rate-limit gate)."""
        t = _task(status="completed")
        decision = decide_spawn(
            task=t,
            trigger=_trigger(
                TriggerKind.NOTIFICATION,
                provider="anthropic",
                provider_rate_limited=True,
            ),
            config=_DEFAULT_CONFIG,
        )
        # Rule 1 fires first — outcome must be DROP, not QUEUE
        assert decision.outcome == SpawnDecision.DROP

    def test_rate_limit_gate_fires_before_claimant_lock(self) -> None:
        """Rule 2 (rate-limit gate) fires before rule 3 (single-claimant invariant)."""
        recent = datetime.now(tz=UTC)
        t = _task(
            status="in_progress",
            active_claimant_id=uuid4(),
            last_heartbeat_at=recent,
        )
        decision = decide_spawn(
            task=t,
            trigger=_trigger(
                TriggerKind.NOTIFICATION,
                provider="anthropic",
                provider_rate_limited=True,
            ),
            config=_DEFAULT_CONFIG,
        )
        # Both gates would QUEUE but reason must come from rate-limit (rule 2)
        assert decision.outcome == SpawnDecision.QUEUE
        assert "rate-limited" in decision.reason

"""Cross-tick cooldown for notification-triggered spawns.

Escalation/approval/audit/a2a dispatchers carry no task_id, so neither the
readiness gate nor the PM respawn breaker sees them — the cooldown is the
loop-breaker that stops an unacknowledged notification from respawning its
recipient every dispatch tick.
"""

from __future__ import annotations

from unittest.mock import patch

from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._notification_spawn_at = {}
    return orch


def test_first_spawn_allowed_then_damped() -> None:
    orch = _orch()
    with patch.object(settings, "notification_spawn_cooldown_seconds", 600):
        assert orch._notification_spawn_cooled("be-pm", "n1") is False
        assert orch._notification_spawn_cooled("be-pm", "n1") is True
        # A different notification or agent is independent.
        assert orch._notification_spawn_cooled("be-pm", "n2") is False
        assert orch._notification_spawn_cooled("fe-pm", "n1") is False


def test_cooldown_expires() -> None:
    orch = _orch()
    with (
        patch.object(settings, "notification_spawn_cooldown_seconds", 600),
        patch("roboco.runtime.orchestrator.time.monotonic") as clock,
    ):
        clock.return_value = 1_000.0
        assert orch._notification_spawn_cooled("be-pm", "n1") is False
        clock.return_value = 1_300.0  # inside the window
        assert orch._notification_spawn_cooled("be-pm", "n1") is True
        clock.return_value = 1_700.0  # window elapsed → retry allowed
        assert orch._notification_spawn_cooled("be-pm", "n1") is False


def test_zero_cooldown_disables_damper() -> None:
    orch = _orch()
    with patch.object(settings, "notification_spawn_cooldown_seconds", 0):
        assert orch._notification_spawn_cooled("be-pm", "n1") is False
        assert orch._notification_spawn_cooled("be-pm", "n1") is False


def test_missing_notification_id_never_damped() -> None:
    orch = _orch()
    with patch.object(settings, "notification_spawn_cooldown_seconds", 600):
        assert orch._notification_spawn_cooled("be-pm", None) is False
        assert orch._notification_spawn_cooled("be-pm", None) is False
        assert orch._notification_spawn_at == {}


def test_map_prunes_expired_entries() -> None:
    orch = _orch()
    prune_at = AgentOrchestrator._NOTIFICATION_COOLDOWN_PRUNE_AT
    with (
        patch.object(settings, "notification_spawn_cooldown_seconds", 600),
        patch("roboco.runtime.orchestrator.time.monotonic") as clock,
    ):
        clock.return_value = 1_000.0
        for i in range(prune_at + 1):
            orch._notification_spawn_cooled("be-pm", f"n{i}")
        assert len(orch._notification_spawn_at) > prune_at
        # All entries expired → the next insert prunes them down to ~the
        # fresh entry (plus at most the just-stamped one).
        _max_after_prune = 2
        clock.return_value = 2_000.0
        orch._notification_spawn_cooled("be-pm", "fresh")
        assert len(orch._notification_spawn_at) <= _max_after_prune

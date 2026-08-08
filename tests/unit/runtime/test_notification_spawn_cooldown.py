"""Cross-tick cooldown for notification-triggered spawns.

Escalation/approval/audit/a2a dispatchers carry no task_id, so neither the
readiness gate nor the PM respawn breaker sees them — the cooldown is the
loop-breaker that stops an unacknowledged notification from respawning its
recipient every dispatch tick.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._notification_spawn_at = {}
    return orch


async def test_first_spawn_allowed_then_damped() -> None:
    orch = _orch()
    with patch.object(settings, "notification_spawn_cooldown_seconds", 600):
        assert await orch._notification_spawn_cooled("be-pm", "n1") is False
        assert await orch._notification_spawn_cooled("be-pm", "n1") is True
        # A different notification or agent is independent.
        assert await orch._notification_spawn_cooled("be-pm", "n2") is False
        assert await orch._notification_spawn_cooled("fe-pm", "n1") is False


async def test_cooldown_expires() -> None:
    orch = _orch()
    with (
        patch.object(settings, "notification_spawn_cooldown_seconds", 600),
        patch("roboco.runtime.orchestrator.time.monotonic") as clock,
    ):
        clock.return_value = 1_000.0
        assert await orch._notification_spawn_cooled("be-pm", "n1") is False
        clock.return_value = 1_300.0  # inside the window
        assert await orch._notification_spawn_cooled("be-pm", "n1") is True
        clock.return_value = 1_700.0  # window elapsed → retry allowed
        assert await orch._notification_spawn_cooled("be-pm", "n1") is False


async def test_zero_cooldown_disables_damper() -> None:
    orch = _orch()
    with patch.object(settings, "notification_spawn_cooldown_seconds", 0):
        assert await orch._notification_spawn_cooled("be-pm", "n1") is False
        assert await orch._notification_spawn_cooled("be-pm", "n1") is False


async def test_missing_notification_id_never_damped() -> None:
    orch = _orch()
    with patch.object(settings, "notification_spawn_cooldown_seconds", 600):
        assert await orch._notification_spawn_cooled("be-pm", None) is False
        assert await orch._notification_spawn_cooled("be-pm", None) is False
        assert orch._notification_spawn_at == {}


async def test_hard_cap_breaks_respawn_loop() -> None:
    """Past max_attempts spawns for one unacked notification, the target is
    suppressed forever — the loop breaker for no-task_id escalations."""
    orch = _orch()
    with (
        patch.object(settings, "notification_spawn_cooldown_seconds", 600),
        patch.object(settings, "notification_spawn_max_attempts", 3),
        patch("roboco.runtime.orchestrator.time.monotonic") as clock,
        patch.object(
            AgentOrchestrator, "_notify_notification_spawn_capped", AsyncMock()
        ),
    ):
        # Each retry lands in a fresh cooldown window (advance past 600s).
        for i in range(3):
            clock.return_value = 1_000.0 + i * 700
            assert await orch._notification_spawn_cooled("fe-pm", "stuck") is False
        # 4th+ window: cap tripped — suppressed despite the cooldown elapsing.
        for i in range(3, 8):
            clock.return_value = 1_000.0 + i * 700
            assert await orch._notification_spawn_cooled("fe-pm", "stuck") is True
        # A different notification is unaffected by another's cap.
        clock.return_value = 9_000.0
        assert await orch._notification_spawn_cooled("fe-pm", "other") is False


async def test_zero_max_attempts_disables_cap() -> None:
    orch = _orch()
    with (
        patch.object(settings, "notification_spawn_cooldown_seconds", 600),
        patch.object(settings, "notification_spawn_max_attempts", 0),
        patch("roboco.runtime.orchestrator.time.monotonic") as clock,
    ):
        # Cap off: only the cooldown gates, every elapsed window respawns.
        for i in range(20):
            clock.return_value = 1_000.0 + i * 700
            assert await orch._notification_spawn_cooled("fe-pm", "stuck") is False


async def test_cap_survives_prune() -> None:
    """A capped entry stays suppressed even after a prune sweep fires (the
    prune must not drop the count and reset the cap)."""
    orch = _orch()
    prune_at = AgentOrchestrator._NOTIFICATION_COOLDOWN_PRUNE_AT
    with (
        patch.object(settings, "notification_spawn_cooldown_seconds", 600),
        patch.object(settings, "notification_spawn_max_attempts", 2),
        patch("roboco.runtime.orchestrator.time.monotonic") as clock,
        patch.object(
            AgentOrchestrator, "_notify_notification_spawn_capped", AsyncMock()
        ),
    ):
        clock.return_value = 10_000.0
        assert await orch._notification_spawn_cooled("fe-pm", "stuck") is False
        clock.return_value = 10_700.0
        assert await orch._notification_spawn_cooled("fe-pm", "stuck") is False
        clock.return_value = 11_400.0
        assert await orch._notification_spawn_cooled("fe-pm", "stuck") is True  # capped
        # Force a prune sweep with many fresh keys.
        for i in range(prune_at + 1):
            await orch._notification_spawn_cooled("be-pm", f"n{i}")
        # Advance well past the cooldown so only the surviving cap — not the
        # cooldown — can suppress the next "stuck" check. A prune that dropped
        # the count would reset the cap and allow a spawn (return False) here.
        clock.return_value = 20_000.0
        assert await orch._notification_spawn_cooled("fe-pm", "stuck") is True


async def test_map_prunes_expired_entries() -> None:
    orch = _orch()
    prune_at = AgentOrchestrator._NOTIFICATION_COOLDOWN_PRUNE_AT
    with (
        patch.object(settings, "notification_spawn_cooldown_seconds", 600),
        patch("roboco.runtime.orchestrator.time.monotonic") as clock,
    ):
        clock.return_value = 1_000.0
        for i in range(prune_at + 1):
            await orch._notification_spawn_cooled("be-pm", f"n{i}")
        assert len(orch._notification_spawn_at) > prune_at
        # All entries expired → the next insert prunes them down to ~the
        # fresh entry (plus at most the just-stamped one).
        _max_after_prune = 2
        clock.return_value = 2_000.0
        await orch._notification_spawn_cooled("be-pm", "fresh")
        assert len(orch._notification_spawn_at) <= _max_after_prune


async def test_cap_trip_sends_one_ceo_notification() -> None:
    """Crossing attempts==max_attempts sends exactly one CEO notification;
    later suppressed spawns for the same key send no more."""
    orch = _orch()
    with (
        patch.object(settings, "notification_spawn_cooldown_seconds", 600),
        patch.object(settings, "notification_spawn_max_attempts", 2),
        patch("roboco.runtime.orchestrator.time.monotonic") as clock,
        patch(
            "roboco.services.notification.NotificationService"
            ".send_notification_spawn_cap_notification",
            new_callable=AsyncMock,
        ) as send_mock,
    ):
        # Two allowed spawns build the count to max_attempts.
        clock.return_value = 1_000.0
        assert await orch._notification_spawn_cooled("fe-pm", "stuck") is False
        clock.return_value = 1_700.0
        assert await orch._notification_spawn_cooled("fe-pm", "stuck") is False
        send_mock.assert_not_called()

        # This is the trip: attempts == max_attempts.
        clock.return_value = 2_400.0
        assert await orch._notification_spawn_cooled("fe-pm", "stuck") is True
        send_mock.assert_awaited_once()
        _, kwargs = send_mock.call_args
        assert kwargs["agent_slug"] == "fe-pm"
        assert kwargs["notification_id"] == "stuck"
        assert kwargs["to_agent"] == "ceo"

        # Every subsequent suppressed spawn for the same key must not re-fire.
        for i in range(3, 8):
            clock.return_value = 1_000.0 + i * 700
            assert await orch._notification_spawn_cooled("fe-pm", "stuck") is True
        send_mock.assert_awaited_once()


async def test_notification_failure_swallowed() -> None:
    """A notification-send failure at cap-trip is caught and logged, never
    raised into the caller."""
    orch = _orch()
    with (
        patch.object(settings, "notification_spawn_cooldown_seconds", 600),
        patch.object(settings, "notification_spawn_max_attempts", 1),
        patch("roboco.runtime.orchestrator.time.monotonic") as clock,
        patch(
            "roboco.services.notification.NotificationService"
            ".send_notification_spawn_cap_notification",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
    ):
        clock.return_value = 1_000.0
        assert await orch._notification_spawn_cooled("fe-pm", "stuck") is False
        clock.return_value = 1_700.0
        # Cap trips here; the notification failure must not raise.
        assert await orch._notification_spawn_cooled("fe-pm", "stuck") is True

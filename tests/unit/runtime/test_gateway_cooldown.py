"""Gateway spawn-cooldown gate.

`gateway_pre_spawn_check` always reaches
``roboco.services.gateway.trigger_filter.decide_spawn`` (except the no-task
carve-out), whose 4-rule cooldown machinery is the real spawn gate.

Without these assertions a regression that drops the call site would leave
the orchestrator with no server-side spawn cooldown beyond
``_pm_respawn_should_gate``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import gateway_pre_spawn_check
from roboco.services.gateway.trigger_filter import Decision, SpawnDecision


@pytest.mark.asyncio
async def test_gateway_enabled_consults_decide_spawn() -> None:
    """``decide_spawn`` is invoked and its decision propagates."""
    task_id = str(uuid4())

    # Stub task row that decide_spawn will receive.
    fake_task_row = MagicMock()
    fake_task_row.status = "pending"
    fake_task_row.active_claimant_id = None
    fake_task_row.last_heartbeat_at = None

    # Stub the async DB session: count queries return 0, task lookup returns
    # our fake row. ``async with factory() as db`` -> ``db.execute(...)``.
    fake_count_result = MagicMock()
    fake_count_result.scalars.return_value.all.return_value = []

    fake_task_result = MagicMock()
    fake_task_result.scalars.return_value.first.return_value = fake_task_row

    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(
        side_effect=[fake_count_result, fake_count_result, fake_task_result]
    )
    fake_db.add = MagicMock()
    fake_db.flush = AsyncMock()
    fake_db.commit = AsyncMock()

    fake_factory = MagicMock()
    fake_factory.return_value.__aenter__ = AsyncMock(return_value=fake_db)
    fake_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    expected = Decision(SpawnDecision.QUEUE, "per-task spawn cooldown active")

    with (
        patch("roboco.db.base.get_session_factory", return_value=fake_factory),
        patch(
            "roboco.services.gateway.trigger_filter.decide_spawn",
            return_value=expected,
        ) as mock_decide_spawn,
    ):
        outcome, reason = await gateway_pre_spawn_check(
            task_id=task_id,
            trigger_kind="scan",
            target_role="developer",
        )

    mock_decide_spawn.assert_called_once()
    call_kwargs = mock_decide_spawn.call_args.kwargs
    assert call_kwargs["task"] is fake_task_row
    assert call_kwargs["trigger"].kind.value == "scan"
    assert call_kwargs["config"].cooldown_seconds > 0

    assert outcome == "queue"
    assert reason == "per-task spawn cooldown active"


@pytest.mark.asyncio
async def test_gateway_enabled_skips_decide_spawn_when_no_task_id() -> None:
    """No task_id -> early return; ``decide_spawn`` not called.

    Documents the no-task-spawn carve-out: idle PM ticks pass through.
    """
    with patch(
        "roboco.services.gateway.trigger_filter.decide_spawn"
    ) as mock_decide_spawn:
        outcome, reason = await gateway_pre_spawn_check(
            task_id=None,
            trigger_kind="scan",
            target_role="main_pm",
        )

    assert outcome == "spawn"
    assert "no task_id" in reason
    mock_decide_spawn.assert_not_called()

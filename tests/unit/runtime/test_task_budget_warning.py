"""Pre-breach (80%) CEO budget warning: policy helper, sweep hook, delivery dedup."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.foundation.policy.agent_loop import budget_warning_due
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.notification_delivery import NotificationDeliveryService


@pytest.mark.parametrize(
    ("cap", "spend", "due"),
    [
        (10.0, 7.99, False),
        (10.0, 8.0, True),
        (10.0, 9.99, True),
        (10.0, 10.0, False),
        (10.0, 12.0, False),
        (0.0, 0.0, False),
    ],
)
def test_budget_warning_due_window(cap: float, spend: float, due: bool) -> None:
    assert budget_warning_due(cap, spend) is due


def _db() -> MagicMock:
    db = MagicMock()

    @asynccontextmanager
    async def _nested() -> Any:
        yield

    db.begin_nested = _nested
    return db


@pytest.mark.asyncio
async def test_sweep_warns_only_inside_window() -> None:
    task = MagicMock(id=uuid4())
    delivery = MagicMock()
    delivery.notify_ceo_of_budget_warning = AsyncMock()
    with patch(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        return_value=delivery,
    ):
        warn = AgentOrchestrator._warn_task_budget_nearing_cap
        await warn(_db(), task, cap_usd=10.0, spend_usd=5.0)
        delivery.notify_ceo_of_budget_warning.assert_not_awaited()
        await warn(_db(), task, cap_usd=10.0, spend_usd=8.5)
        delivery.notify_ceo_of_budget_warning.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_warning_failure_is_swallowed() -> None:
    task = MagicMock(id=uuid4())
    delivery = MagicMock()
    delivery.notify_ceo_of_budget_warning = AsyncMock(side_effect=RuntimeError("x"))
    with patch(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        return_value=delivery,
    ):
        await AgentOrchestrator._warn_task_budget_nearing_cap(
            _db(), task, cap_usd=10.0, spend_usd=9.0
        )


class _Svc(NotificationDeliveryService):
    def __init__(self, existing: object | None) -> None:
        result = MagicMock()
        result.first.return_value = existing
        self.session = MagicMock(execute=AsyncMock(return_value=result))
        self.persist = AsyncMock(return_value=True)

    async def _get_ceo_agent(self) -> Any:
        return MagicMock(id=uuid4())

    async def _persist_and_deliver(self, notification: Any, **_: Any) -> bool:
        return bool(await self.persist(notification))


@pytest.mark.asyncio
async def test_warning_sent_once_and_deduped() -> None:
    task = MagicMock(title="T", assigned_to=None)
    task_id = uuid4()
    fresh = _Svc(None)
    assert await fresh.notify_ceo_of_budget_warning(
        task=task, task_id=task_id, cap_usd=10.0, spend_usd=8.0
    )
    sent = fresh.persist.await_args_list[0].args[0]
    assert sent.subject == "Budget warning: T"
    assert "80%" in sent.body

    dup = _Svc(("row",))
    assert not await dup.notify_ceo_of_budget_warning(
        task=task, task_id=task_id, cap_usd=10.0, spend_usd=8.0
    )
    dup.persist.assert_not_awaited()

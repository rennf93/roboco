"""The 'is there actually live work' gate for notification-triggered spawns.

Escalation/approval dispatchers must not revive an agent for a notification
that has expired, is stale past the spawn-age window, or whose related task is
already terminal — otherwise a wedged/old notification loops the fleet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    # _api_url is a read-only property (settings.internal_api_url); the mock
    # client below ignores the URL, so no wiring is needed.
    return AgentOrchestrator.__new__(AgentOrchestrator)


def _client(task_status: str | None = None, *, fail: bool = False) -> Any:
    client = MagicMock()
    if fail:
        client.get = AsyncMock(side_effect=RuntimeError("boom"))
        return client
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"status": task_status})
    client.get = AsyncMock(return_value=resp)
    return client


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.mark.asyncio
async def test_expired_notification_has_no_work() -> None:
    orch = _orch()
    notif = {"expires_at": _iso(datetime.now(UTC) - timedelta(minutes=1))}
    assert await orch._notification_has_live_work(_client(), notif) is False


@pytest.mark.asyncio
async def test_stale_notification_has_no_work() -> None:
    orch = _orch()
    old = datetime.now(UTC) - timedelta(
        seconds=settings.notification_spawn_max_age_seconds + 60
    )
    notif = {"timestamp": _iso(old)}
    assert await orch._notification_has_live_work(_client(), notif) is False


@pytest.mark.asyncio
async def test_terminal_related_task_has_no_work() -> None:
    orch = _orch()
    notif = {"timestamp": _iso(datetime.now(UTC)), "related_task_id": "t1"}
    assert await orch._notification_has_live_work(_client("completed"), notif) is False
    assert await orch._notification_has_live_work(_client("cancelled"), notif) is False


@pytest.mark.asyncio
async def test_fresh_notification_with_live_task_has_work() -> None:
    orch = _orch()
    notif = {"timestamp": _iso(datetime.now(UTC)), "related_task_id": "t1"}
    assert await orch._notification_has_live_work(_client("in_progress"), notif) is True


@pytest.mark.asyncio
async def test_fresh_notification_no_task_has_work() -> None:
    orch = _orch()
    notif = {"timestamp": _iso(datetime.now(UTC))}
    assert await orch._notification_has_live_work(_client(), notif) is True


@pytest.mark.asyncio
async def test_fail_open_on_fetch_error_and_bad_timestamp() -> None:
    orch = _orch()
    # A failed task fetch must not suppress a real escalation.
    notif = {"timestamp": _iso(datetime.now(UTC)), "related_task_id": "t1"}
    assert await orch._notification_has_live_work(_client(fail=True), notif) is True
    # An unparseable timestamp is ignored (no false-stale), not treated as old.
    assert (
        await orch._notification_has_live_work(_client(), {"timestamp": "nope"}) is True
    )


@pytest.mark.asyncio
async def test_staleness_gate_disabled_when_zero() -> None:
    orch = _orch()
    ancient = datetime.now(UTC) - timedelta(days=30)
    notif = {"timestamp": _iso(ancient)}
    with patch.object(settings, "notification_spawn_max_age_seconds", 0):
        assert await orch._notification_has_live_work(_client(), notif) is True

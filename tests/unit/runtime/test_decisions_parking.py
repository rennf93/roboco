"""Wiring test for the parking pilot (spec 6.2) at the spawn_exit seam:
fail-open to PARK_STANDARD when anything under the pilot fails."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.runtime.engines.spawn_exit import (
    _DECISIONS_RETRY_SOON_RETRY_AFTER_S,
    SpawnExitEngine,
)
from roboco.services import decisions


def _bare():
    return SpawnExitEngine.__new__(SpawnExitEngine)


@pytest.mark.asyncio
async def test_parking_lane_fail_open_when_db_unavailable(monkeypatch):
    engine = _bare()
    # get_session_factory resolves inside the helper; give it a factory whose
    # session context blows up immediately.
    import roboco.db.base as db_base

    class _BoomFactory:
        def __call__(self):
            raise RuntimeError("no db")

    monkeypatch.setattr(db_base, "get_session_factory", _BoomFactory)
    lane = await engine._decisions_parking_lane(
        "backend-dev-1", provider="grok", kind="rate_limited", task_id=None
    )
    assert lane is decisions.ParkingLane.PARK_STANDARD


@pytest.mark.asyncio
async def test_parking_lane_returns_pilot_verdict(monkeypatch):
    engine = _bare()
    import roboco.db.base as db_base
    import roboco.services.task as task_module

    db = MagicMock()
    task_svc = MagicMock()
    task_svc.list_in_progress_or_claimed = AsyncMock(return_value=[])

    class _FakeFactory:
        def __call__(self):
            fake_cm = MagicMock()
            fake_cm.__aenter__ = AsyncMock(return_value=db)
            fake_cm.__aexit__ = AsyncMock(return_value=False)
            return fake_cm

    monkeypatch.setattr(db_base, "get_session_factory", _FakeFactory)
    monkeypatch.setattr(
        task_module, "get_task_service", MagicMock(return_value=task_svc)
    )
    monkeypatch.setattr(
        decisions,
        "parking_route",
        AsyncMock(return_value=decisions.ParkingLane.RETRY_SOON),
    )
    engine._make_tracker = MagicMock(
        return_value=MagicMock(get_state=AsyncMock(return_value={}))
    )
    lane = await engine._decisions_parking_lane(
        "backend-dev-1", provider="grok", kind="rate_limited", task_id=None
    )
    assert lane is decisions.ParkingLane.RETRY_SOON


def test_retry_soon_window_is_shorter_than_the_standard_ladder():
    from roboco.runtime.orchestrator import _RATE_LIMIT_RETRY_AFTER_S

    assert _DECISIONS_RETRY_SOON_RETRY_AFTER_S < _RATE_LIMIT_RETRY_AFTER_S

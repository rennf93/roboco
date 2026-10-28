"""Unit tests for TaskService.stuck_state(s) — the task-payload stuck fields.

The wedge ledger marker is the single source of truth for strikes; here the
marker-reading helper (``roboco.services.wedge_ledger.get_wedge_state``) is
stubbed so the mapping contract is tested in isolation: strikes summed over
statuses, uptime-adjusted active time anchored on the marker's movement
timestamp, and the cycle only rendered for a tripped ledger.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable
    from sqlalchemy.ext.asyncio import AsyncSession

_STRIKES_IN_PROGRESS = 2
_STRIKES_NEEDS_REVISION = 1
_STRIKES_TRIPPED = 4
_HOURS_SINCE_UPDATE = 5


def _install_fake_wedge_ledger() -> None:
    """Stub the sibling slice's marker reader (not yet on this branch)."""
    if "roboco.services.wedge_ledger" in sys.modules:
        return

    def get_wedge_state(task: Any) -> dict[str, Any]:
        markers = getattr(task, "orchestration_markers", None) or {}
        state = markers.get("wedge_ledger")
        return state if isinstance(state, dict) else {}

    module: Any = types.ModuleType("roboco.services.wedge_ledger")
    module.get_wedge_state = get_wedge_state
    sys.modules["roboco.services.wedge_ledger"] = module


def _task(*, markers: dict | None = None) -> TaskTable:
    now = datetime.now(UTC)
    return cast(
        "TaskTable",
        SimpleNamespace(
            id=uuid4(),
            orchestration_markers=markers,
            updated_at=now - timedelta(hours=_HOURS_SINCE_UPDATE),
            created_at=now - timedelta(days=2),
        ),
    )


@pytest.fixture()
def service(monkeypatch: pytest.MonkeyPatch) -> TaskService:
    _install_fake_wedge_ledger()
    svc = TaskService(session=cast("AsyncSession", object()))

    class _FakeLedger:
        def active_seconds(self, start: datetime, end: datetime) -> float:
            return (end - start).total_seconds()

    async def _load(_now: datetime) -> Any:
        return _FakeLedger()

    async def _cycle(_task_id: Any, _limit: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "actor": "qa",
                "verb": "awaiting_qa->needs_revision",
                "timestamp": datetime.now(UTC),
            }
        ]

    monkeypatch.setattr(svc, "_load_uptime_ledger", _load)
    monkeypatch.setattr(svc, "_wedge_cycle_entries", _cycle)
    return svc


@pytest.mark.asyncio
async def test_no_ledger_marker_defaults(service: TaskService) -> None:
    stuck = await service.stuck_state(_task())
    assert stuck["open_wedge_strikes"] == 0
    assert stuck["wedge_cycle"] == []
    # No marker movement timestamp: falls back to the task row timestamps.
    assert stuck["active_time_since_progress"] == pytest.approx(
        _HOURS_SINCE_UPDATE * 3600, rel=1e-3
    )


@pytest.mark.asyncio
async def test_strikes_summed_and_cycle_only_when_tripped(
    service: TaskService,
) -> None:
    marker = {
        "wedge_ledger": {
            "progress_fp": [3, 1, 0],
            "strikes": {
                "in_progress": _STRIKES_IN_PROGRESS,
                "needs_revision": _STRIKES_NEEDS_REVISION,
            },
            "tripped": False,
            "last_progress_at": "2026-09-29T10:00:00+00:00",
        }
    }
    stuck = await service.stuck_state(_task(markers=marker))
    assert stuck["open_wedge_strikes"] == (
        _STRIKES_IN_PROGRESS + _STRIKES_NEEDS_REVISION
    )
    assert stuck["wedge_cycle"] == []
    assert stuck["active_time_since_progress"] == pytest.approx(
        (
            datetime.now(UTC) - datetime.fromisoformat("2026-09-29T10:00:00+00:00")
        ).total_seconds(),
        rel=1e-3,
    )


@pytest.mark.asyncio
async def test_tripped_ledger_renders_cycle(service: TaskService) -> None:
    marker = {
        "wedge_ledger": {
            "progress_fp": [3, 1, 0],
            "strikes": {"in_progress": _STRIKES_TRIPPED},
            "tripped": True,
            "tripped_at": "2026-09-29T12:00:00+00:00",
            "tripped_strikes": _STRIKES_TRIPPED,
        }
    }
    stuck = await service.stuck_state(_task(markers=marker))
    assert stuck["open_wedge_strikes"] == _STRIKES_TRIPPED
    assert len(stuck["wedge_cycle"]) == 1
    assert stuck["wedge_cycle"][0]["actor"] == "qa"

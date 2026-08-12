"""An ``engines``-scope maintenance pause suppresses the originating
engines' autonomous work: CI-watch, dep-update, docs-sync, release manager,
env-sync, X, video, self-heal all carry the identical one-line
``is_paused(self.session, PauseScope.ENGINES)`` check next to their existing
``settings.xxx_enabled`` gate (see the report for the full file:line list).
This file exercises two representative engines plus self-heal's one nuance:
a detected regression still NOTIFIES the CEO while paused, only the fix-task
ORIGINATION is suppressed, since a CEO alert is a signal, not autonomous
action.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
import roboco.services.self_heal_engine as sh_module
from roboco.config import settings as cfg
from roboco.foundation.policy.maintenance_pause import PauseScope
from roboco.services.ci_watch_engine import CiWatchEngine
from roboco.services.maintenance_pause import get_maintenance_pause_service
from roboco.services.self_heal_engine import RegressionObservation, SelfHealEngine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _pause_engines(session: AsyncSession) -> None:
    await get_maintenance_pause_service(session).pause(
        PauseScope.ENGINES, by="ceo", hours=1
    )


@pytest.mark.asyncio
async def test_ci_watch_run_cycle_no_ops_while_paused(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "ci_watch_enabled", True)
    await _pause_engines(db_session)
    fake_source = AsyncMock()
    engine = CiWatchEngine(db_session, source=fake_source)

    result = await engine.run_cycle([])

    assert result == []
    fake_source.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_ci_watch_run_cycle_probes_when_not_paused(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity check for the test above: proves the injected source is really
    reached once unpaused, so the paused assertion isn't vacuous."""
    monkeypatch.setattr(cfg, "ci_watch_enabled", True)
    fake_source = AsyncMock()
    fake_source.fetch.return_value = []
    engine = CiWatchEngine(db_session, source=fake_source)

    await engine.run_cycle([])

    fake_source.fetch.assert_awaited_once()


class _FakeNotifier:
    def __init__(self) -> None:
        self.send_ack_notification = AsyncMock()


@pytest.mark.asyncio
async def test_self_heal_notifies_but_does_not_originate_while_paused(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "self_heal_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_originate_enabled", True)
    await _pause_engines(db_session)

    fake_source = AsyncMock()
    engine = SelfHealEngine(db_session, source=fake_source)
    observation = RegressionObservation(
        fingerprint="fp1",
        signal_name="ci",
        repo_hint="roboco",
        summary="CI red",
        detail="workflow failed",
        raw_ref="",
    )
    monkeypatch.setattr(engine, "assess", AsyncMock(return_value=[observation]))
    monkeypatch.setattr(engine, "_already_notified", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_mark_notified", AsyncMock())
    monkeypatch.setattr(
        engine, "_open_self_heal_task_ids_by_fp", AsyncMock(return_value={})
    )
    originate_mock = AsyncMock()
    monkeypatch.setattr(engine, "_originate", originate_mock)
    fake_notifier = _FakeNotifier()
    monkeypatch.setattr(sh_module, "NotificationService", lambda: fake_notifier)

    observations = await engine.run_cycle()

    assert observations == [observation]
    fake_notifier.send_ack_notification.assert_awaited_once()  # notify preserved
    originate_mock.assert_not_awaited()  # origination suppressed

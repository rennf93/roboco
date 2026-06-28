"""The orchestrator CI-watch loop: dormant when off, runs the engine when on.

Dormant unless ``ci_watch_enabled``; loads the watch set (opted-in projects, one
per repo), warns when enabled-but-empty, and runs CiWatchEngine.run_cycle each
interval. Separate from the single-repo self-heal loop.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> Any:
    return AgentOrchestrator.__new__(AgentOrchestrator)


@pytest.mark.asyncio
async def test_loop_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ci_watch_enabled", False)
    orch = _orch()
    cycle = AsyncMock()
    orch._run_ci_watch_cycle = cycle
    await orch._ci_watch_loop()  # must return immediately, no infinite loop
    cycle.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_watch_set_filters_enabled_one_per_repo() -> None:
    orch = _orch()
    on_a = MagicMock(slug="be", git_url="https://x/a.git", ci_watch_enabled=True)
    on_a2 = MagicMock(slug="fe", git_url="https://x/a.git", ci_watch_enabled=True)
    off = MagicMock(slug="c", git_url="https://x/c.git", ci_watch_enabled=False)
    svc = MagicMock()
    svc.list_all = AsyncMock(return_value=[on_a, on_a2, off])
    with patch("roboco.services.project.get_project_service", return_value=svc):
        watch = await orch._load_ci_watch_set(MagicMock())
    assert len(watch) == 1  # opt-out excluded; same-repo cell-projects collapsed
    assert watch[0].git_url == "https://x/a.git"


def _db_ctx(db: Any) -> Any:
    @asynccontextmanager
    async def _ctx() -> Any:
        yield db

    return _ctx


@pytest.mark.asyncio
async def test_cycle_warns_and_skips_engine_when_empty() -> None:
    orch = _orch()
    orch._load_ci_watch_set = AsyncMock(return_value=[])
    get_eng = MagicMock()
    with (
        patch("roboco.db.get_db_context", _db_ctx(MagicMock())),
        patch("roboco.services.ci_watch_engine.get_ci_watch_engine", get_eng),
    ):
        await orch._run_ci_watch_cycle()
    get_eng.assert_not_called()  # empty watch set → no engine run


@pytest.mark.asyncio
async def test_cycle_runs_engine_when_watch_set_present() -> None:
    orch = _orch()
    watch = [MagicMock()]
    orch._load_ci_watch_set = AsyncMock(return_value=watch)
    db = MagicMock()
    db.commit = AsyncMock()
    engine = MagicMock()
    engine.run_cycle = AsyncMock(return_value=[])
    with (
        patch("roboco.db.get_db_context", _db_ctx(db)),
        patch(
            "roboco.services.ci_watch_engine.get_ci_watch_engine",
            return_value=engine,
        ) as get_eng,
    ):
        await orch._run_ci_watch_cycle()
    get_eng.assert_called_once()
    engine.run_cycle.assert_awaited_once_with(watch)
    db.commit.assert_awaited_once()

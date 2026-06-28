"""The orchestrator dep-update loop: dormant when off, runs the engine when on.

Dormant unless ``dep_update_enabled``; loads the eligible set (projects with a
dep_update_command, one per repo), warns when enabled-but-empty, and runs
DepUpdateEngine.run_cycle each interval. Separate from self-heal/CI-watch loops.
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
    monkeypatch.setattr(settings, "dep_update_enabled", False)
    orch = _orch()
    cycle = AsyncMock()
    orch._run_dep_update_cycle = cycle
    await orch._dep_update_loop()
    cycle.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_set_filters_command_one_per_repo() -> None:
    orch = _orch()
    on_a = MagicMock(slug="be", git_url="https://x/a.git", dep_update_command="uv -U")
    on_a2 = MagicMock(slug="fe", git_url="https://x/a.git", dep_update_command="uv -U")
    off = MagicMock(slug="c", git_url="https://x/c.git", dep_update_command=None)
    svc = MagicMock()
    svc.list_all = AsyncMock(return_value=[on_a, on_a2, off])
    with patch("roboco.services.project.get_project_service", return_value=svc):
        eligible = await orch._load_dep_update_set(MagicMock())
    assert len(eligible) == 1  # no-command excluded; same-repo + same-command collapsed
    assert eligible[0].git_url == "https://x/a.git"


@pytest.mark.asyncio
async def test_load_set_keeps_distinct_commands_per_repo() -> None:
    """F115: a monorepo's several cell-projects each carrying their OWN
    ``dep_update_command`` (different ecosystems → different lockfiles) must
    ALL be probed — collapsing to the canonical cell's command would miss the
    other cells' lockfile drift (under-count). Same repo, DIFFERENT commands →
    one entry per (repo, command). The engine's per-git_url open-task dedup
    still prevents duplicate update tasks for the repo."""
    orch = _orch()
    be = MagicMock(
        slug="be", git_url="https://x/a.git", dep_update_command="uv lock --upgrade"
    )
    fe = MagicMock(
        slug="fe", git_url="https://x/a.git", dep_update_command="pnpm update -L"
    )
    svc = MagicMock()
    svc.list_all = AsyncMock(return_value=[be, fe])
    with patch("roboco.services.project.get_project_service", return_value=svc):
        eligible = await orch._load_dep_update_set(MagicMock())
    # distinct commands both probed, NOT collapsed to one canonical cell —
    # the set-equality assertion proves exactly-two (no magic-value literal).
    commands = {p.dep_update_command for p in eligible}
    assert commands == {"uv lock --upgrade", "pnpm update -L"}


def _db_ctx(db: Any) -> Any:
    @asynccontextmanager
    async def _ctx() -> Any:
        yield db

    return _ctx


@pytest.mark.asyncio
async def test_cycle_warns_and_skips_engine_when_empty() -> None:
    orch = _orch()
    orch._load_dep_update_set = AsyncMock(return_value=[])
    get_eng = MagicMock()
    with (
        patch("roboco.db.get_db_context", _db_ctx(MagicMock())),
        patch("roboco.services.dep_update_engine.get_dep_update_engine", get_eng),
    ):
        await orch._run_dep_update_cycle()
    get_eng.assert_not_called()


@pytest.mark.asyncio
async def test_cycle_runs_engine_when_eligible_present() -> None:
    orch = _orch()
    eligible = [MagicMock()]
    orch._load_dep_update_set = AsyncMock(return_value=eligible)
    db = MagicMock()
    db.commit = AsyncMock()
    engine = MagicMock()
    engine.run_cycle = AsyncMock(return_value=[])
    with (
        patch("roboco.db.get_db_context", _db_ctx(db)),
        patch(
            "roboco.services.dep_update_engine.get_dep_update_engine",
            return_value=engine,
        ) as get_eng,
    ):
        await orch._run_dep_update_cycle()
    get_eng.assert_called_once()
    engine.run_cycle.assert_awaited_once_with(eligible)
    db.commit.assert_awaited_once()

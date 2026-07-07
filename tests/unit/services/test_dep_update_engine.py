"""DepUpdateEngine — dedupe by (repo, command) + single open-task fetch.

Two unit tests (no DB): a monorepo with two distinct ``dep_update_command``
values is not blocked by ``git_url`` alone (one open task per command, not one
per repo), and ``run_cycle`` fetches the open-task set exactly once regardless
of project count (the per-project scoped query is gone).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.dep_update_engine import DepUpdateEngine


def _project(slug: str, git_url: str, command: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), slug=slug, git_url=git_url, dep_update_command=command
    )


def _open_task(project: Any) -> SimpleNamespace:
    # run_cycle reads t.project.git_url / t.project.dep_update_command.
    return SimpleNamespace(id=uuid4(), project_id=project.id, project=project)


class _FakeWorkspace:
    def __init__(self, updates: bool = True) -> None:
        self._updates = updates

    async def dry_upgrade_changes_lockfile(self, _project: Any) -> bool:
        return self._updates


def _make_engine(
    task_svc: Any, workspace: _FakeWorkspace | None = None
) -> tuple[DepUpdateEngine, patch[Any, Any]]:
    session = MagicMock()
    engine = DepUpdateEngine(session, workspace=workspace or _FakeWorkspace())
    patcher = patch(
        "roboco.services.dep_update_engine.get_task_service", return_value=task_svc
    )
    patcher.start()
    return engine, patcher


@pytest.fixture
def _enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dep_update_enabled", True)
    monkeypatch.setattr(settings, "dep_update_max_per_cycle", 10)
    monkeypatch.setattr(settings, "dep_update_max_open_tasks", 10)


@pytest.mark.asyncio
async def test_dedupe_spared_by_command_not_git_url(_enabled: None) -> None:
    """A monorepo with two distinct dep_update_command values is not blocked
    by git_url alone — one open task per command, not one per repo."""
    git_url = "https://github.com/x/mono.git"
    p1 = _project("mono-uv", git_url, "uv lock -U")
    p2 = _project("mono-pnpm", git_url, "pnpm update -L")
    open_task = _open_task(p1)

    task_svc = MagicMock()
    task_svc.list_open_dep_update_tasks = AsyncMock(return_value=[open_task])
    task_svc.create = AsyncMock(
        side_effect=lambda req: SimpleNamespace(id=uuid4(), project_id=req.project_id)
    )

    engine, patcher = _make_engine(task_svc)
    try:
        created = await engine.run_cycle([p1, p2])
    finally:
        patcher.stop()

    # p1 deduped (open task already covers (mono, "uv lock -U"));
    # p2 still eligible (different command) -> opened.
    assert len(created) == 1
    assert created[0].project_id == p2.id


@pytest.mark.asyncio
async def test_run_cycle_fetches_open_tasks_once(_enabled: None) -> None:
    """run_cycle fetches the open-task set exactly once regardless of project
    count — the per-project scoped query is gone."""
    project_count = 5
    projects = [
        _project(f"p{i}", f"https://github.com/x/r{i}.git", f"cmd{i}")
        for i in range(project_count)
    ]

    task_svc = MagicMock()
    task_svc.list_open_dep_update_tasks = AsyncMock(return_value=[])
    task_svc.create = AsyncMock(
        side_effect=lambda req: SimpleNamespace(id=uuid4(), project_id=req.project_id)
    )

    engine, patcher = _make_engine(task_svc)
    try:
        created = await engine.run_cycle(projects)
    finally:
        patcher.stop()

    assert len(created) == project_count
    # One fetch for both the cap and the per-(repo, command) dedupe —
    # not one per project.
    assert task_svc.list_open_dep_update_tasks.call_count == 1

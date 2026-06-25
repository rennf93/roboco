"""DepUpdateEngine — open an update-deps task per opted-in project with updates.

Opens a PENDING dep_update task (never merges/approves) when the probe reports
updates; skips projects with no command, no updates, or an already-open task for
the same repo (git_url dedupe); honours per-cycle + rolling caps; dormant when
disabled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, TaskStatus, Team
from roboco.services.dep_update_engine import get_dep_update_engine
from roboco.services.task import DEP_UPDATE_SOURCE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
MAIN_PM_UUID = _foundation.AGENTS["main-pm"].uuid


class _FakeWorkspace:
    def __init__(self, updates: bool = True) -> None:
        self._updates = updates

    async def dry_upgrade_changes_lockfile(self, _project: Any) -> bool:
        return self._updates


async def _get_or_create_agent(
    db: AsyncSession, agent_id: object, role: AgentRole, slug: str
) -> None:
    if await db.get(AgentTable, agent_id) is None:
        db.add(
            AgentTable(
                id=agent_id,
                name=slug,
                slug=f"{slug}-{uuid4().hex[:8]}",
                role=role,
                team=None,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="x",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
        await db.flush()


async def _seed_project(
    db: AsyncSession, slug: str, git_url: str, *, command: str | None = "uv lock -U"
) -> ProjectTable:
    project = ProjectTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        git_url=git_url,
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        dep_update_command=command,
    )
    db.add(project)
    await db.flush()
    return project


@pytest.fixture(autouse=True)
async def _enabled(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dep_update_enabled", True)
    monkeypatch.setattr(settings, "dep_update_max_per_cycle", 5)
    monkeypatch.setattr(settings, "dep_update_max_open_tasks", 5)
    await _get_or_create_agent(db_session, SYSTEM_UUID, AgentRole.SYSTEM, "system")
    await _get_or_create_agent(db_session, MAIN_PM_UUID, AgentRole.MAIN_PM, "main-pm")


@pytest.mark.asyncio
async def test_updates_available_opens_one_task(db_session: AsyncSession) -> None:
    proj = await _seed_project(db_session, "dep-a", "https://github.com/x/a.git")
    engine = get_dep_update_engine(db_session, workspace=_FakeWorkspace(updates=True))

    created = await engine.run_cycle([proj])

    assert len(created) == 1
    task = created[0]
    assert task.project_id == proj.id
    assert task.source == DEP_UPDATE_SOURCE
    assert task.confirmed_by_human is True
    assert task.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_no_command_skipped(db_session: AsyncSession) -> None:
    proj = await _seed_project(
        db_session, "dep-b", "https://github.com/x/b.git", command=None
    )
    engine = get_dep_update_engine(db_session, workspace=_FakeWorkspace(updates=True))
    assert await engine.run_cycle([proj]) == []


@pytest.mark.asyncio
async def test_no_updates_skipped(db_session: AsyncSession) -> None:
    proj = await _seed_project(db_session, "dep-c", "https://github.com/x/c.git")
    engine = get_dep_update_engine(db_session, workspace=_FakeWorkspace(updates=False))
    assert await engine.run_cycle([proj]) == []


@pytest.mark.asyncio
async def test_dedupe_same_repo(db_session: AsyncSession) -> None:
    proj = await _seed_project(db_session, "dep-d", "https://github.com/x/d.git")
    engine = get_dep_update_engine(db_session, workspace=_FakeWorkspace(updates=True))
    first = await engine.run_cycle([proj])
    assert len(first) == 1
    second = await engine.run_cycle([proj])  # still updatable, but already open
    assert second == []


@pytest.mark.asyncio
async def test_per_cycle_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "dep_update_max_per_cycle", 1)
    p1 = await _seed_project(db_session, "dep-e", "https://github.com/x/e.git")
    p2 = await _seed_project(db_session, "dep-f", "https://github.com/x/f.git")
    engine = get_dep_update_engine(db_session, workspace=_FakeWorkspace(updates=True))
    created = await engine.run_cycle([p1, p2])
    assert len(created) == 1


@pytest.mark.asyncio
async def test_disabled_is_noop(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "dep_update_enabled", False)
    proj = await _seed_project(db_session, "dep-g", "https://github.com/x/g.git")
    engine = get_dep_update_engine(db_session, workspace=_FakeWorkspace(updates=True))
    assert await engine.run_cycle([proj]) == []

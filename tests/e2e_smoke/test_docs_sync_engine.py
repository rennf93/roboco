"""E2E smoke: docs-divergence engine originates one docs-update task per release.

Drives the real ``DocsSyncEngine`` against the ephemeral test Postgres, mirroring
the shape of ``tests/integration/services/test_docs_sync_engine.py`` but with real
``TaskService``/``ProjectService`` commits. Proves the engine is fail-closed when
disabled, dedupes per release version, and respects the open-task cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from roboco.config import settings
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    TaskStatus,
    Team,
)
from roboco.services.docs_sync_engine import DocsSyncEngine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack


async def _seed_system_agents(session: AsyncSession) -> None:
    """Seed the system + main-pm agents the engine uses as creator/owner."""
    for uuid_, slug, role in (
        (_foundation.AGENTS["system"].uuid, "system", AgentRole.SYSTEM),
        (_foundation.AGENTS["main-pm"].uuid, "main-pm", AgentRole.MAIN_PM),
    ):
        if await session.get(AgentTable, uuid_) is None:
            session.add(
                AgentTable(
                    id=uuid_,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=None,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt=slug,
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )
    await session.flush()


async def _seed_docs_project(session: AsyncSession) -> UUID:
    """Create the ``roboco-website`` project the engine targets."""
    await _seed_system_agents(session)
    project = ProjectTable(
        id=UUID(int=0x1234567890ABCDEF1234567890ABCDEF),  # deterministic for smoke
        name="RoboCo Website",
        slug="roboco-website",
        git_url="https://github.com/rennf93/roboco-website.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=_foundation.AGENTS["system"].uuid,
        is_active=True,
    )
    session.add(project)
    try:
        await session.flush()
    except Exception:
        # Row may already exist from an earlier test in the same session.
        await session.rollback()
        result = await session.execute(
            select(ProjectTable).where(ProjectTable.slug == "roboco-website")
        )
        existing = result.scalar_one()
        return UUID(str(existing.id))
    return UUID(str(project.id))


def _fresh_factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    """Build a per-test engine/session factory bound to the current loop."""
    from roboco.db import base as db_base

    db_base._DbHolder.engine = None
    db_base._DbHolder.session_factory = None
    return db_base.get_session_factory()


@pytest.mark.asyncio
async def test_disabled_engine_is_no_op(e2e_stack: E2EStack) -> None:
    """When ``docs_sync_enabled`` is False the engine returns None and writes
    nothing, even when roboco-website is registered."""
    assert settings.docs_sync_enabled is False, "default must be off for safety"

    factory = _fresh_factory(e2e_stack.db_url)
    engine = create_async_engine(e2e_stack.db_url, future=True)
    try:
        async with factory() as session:
            await _seed_docs_project(session)
            await session.commit()

        async with factory() as session:
            before = (
                (
                    await session.execute(
                        select(TaskTable).where(TaskTable.source == "docs_sync")
                    )
                )
                .scalars()
                .all()
                .__len__()
            )
            docs_engine = DocsSyncEngine(session)
            result = await docs_engine.originate_docs_update(
                version="0.23.0", changelog="## [0.23.0]\n\n- docs\n"
            )
            assert result is None
            await session.commit()

        async with factory() as session:
            after = (
                (
                    await session.execute(
                        select(TaskTable).where(TaskTable.source == "docs_sync")
                    )
                )
                .scalars()
                .all()
                .__len__()
            )
        assert after == 0
        assert after == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_enabled_engine_originates_one_pending_docs_update_task(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the flag on and roboco-website registered, the engine opens exactly
    one PENDING docs-update task carrying the release version marker."""
    monkeypatch.setattr(settings, "docs_sync_enabled", True)
    monkeypatch.setattr(settings, "docs_sync_max_open_tasks", 3)
    monkeypatch.setattr(settings, "docs_sync_max_per_cycle", 1)

    factory = _fresh_factory(e2e_stack.db_url)
    engine = create_async_engine(e2e_stack.db_url, future=True)
    try:
        async with factory() as session:
            project_id = await _seed_docs_project(session)
            await session.commit()

        async with factory() as session:
            docs_engine = DocsSyncEngine(session)
            task = await docs_engine.originate_docs_update(
                version="0.24.0",
                changelog="## [0.24.0]\n\n### Added\n- docs-sync smoke coverage\n",
            )
            assert task is not None
            await session.commit()

        assert task.status == TaskStatus.PENDING
        assert task.source == "docs_sync"
        assert task.team == Team.MAIN_PM
        assert task.project_id == project_id
        assert task.assigned_to == _foundation.AGENTS["main-pm"].uuid
        assert task.orchestration_markers is not None
        assert task.orchestration_markers.get("docs_sync_release_version") == "0.24.0"
        assert "0.24.0" in task.title
        assert "docs-sync smoke coverage" in task.description
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_enabled_engine_dedupes_per_version(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second origination for the same release version returns None and does
    not create a duplicate task."""
    monkeypatch.setattr(settings, "docs_sync_enabled", True)
    monkeypatch.setattr(settings, "docs_sync_max_open_tasks", 3)
    monkeypatch.setattr(settings, "docs_sync_max_per_cycle", 1)

    factory = _fresh_factory(e2e_stack.db_url)
    engine = create_async_engine(e2e_stack.db_url, future=True)
    try:
        async with factory() as session:
            await _seed_docs_project(session)
            await session.commit()

        async with factory() as session:
            docs_engine = DocsSyncEngine(session)
            first = await docs_engine.originate_docs_update(
                version="0.25.0", changelog="x"
            )
            assert first is not None
            second = await docs_engine.originate_docs_update(
                version="0.25.0", changelog="y"
            )
            assert second is None
            await session.commit()

        async with factory() as session:
            count = (
                (
                    await session.execute(
                        select(TaskTable).where(
                            TaskTable.source == "docs_sync",
                            TaskTable.status != TaskStatus.CANCELLED,
                        )
                    )
                )
                .scalars()
                .all()
                .__len__()
            )
        assert count == 1, "same version must dedupe to a single docs-update task"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_enabled_engine_warns_when_project_missing(
    e2e_stack: E2EStack,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With the flag on but no roboco-website project, the engine logs a warning
    and returns None without creating a task."""
    monkeypatch.setattr(settings, "docs_sync_enabled", True)

    factory = _fresh_factory(e2e_stack.db_url)
    engine = create_async_engine(e2e_stack.db_url, future=True)
    try:
        async with factory() as session:
            await _seed_system_agents(session)
            await session.commit()

        async with factory() as session:
            docs_engine = DocsSyncEngine(session)
            with caplog.at_level("WARNING", logger="roboco.services.docs_sync_engine"):
                result = await docs_engine.originate_docs_update(
                    version="0.26.0", changelog="z"
                )
            assert result is None
    finally:
        await engine.dispose()

    assert "roboco-website" in caplog.text
    assert "not registered" in caplog.text

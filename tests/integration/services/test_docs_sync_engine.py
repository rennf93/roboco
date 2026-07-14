"""DocsSyncEngine — originate one docs-update task per release, bounded + deduped.

Mirrors the dep-update engine unit-test style: mocked TaskService/ProjectService
so the engine's logic can be exercised without a real Postgres + pgvector setup.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.docs_sync_engine import DocsSyncEngine


def _project(project_id: Any, slug: str, git_url: str) -> SimpleNamespace:
    return SimpleNamespace(id=project_id, slug=slug, git_url=git_url)


def _task(task_id: Any, project_id: Any, version: str | None = None) -> SimpleNamespace:
    markers: dict[str, Any] = {}
    if version is not None:
        markers["docs_sync_release_version"] = version
    return SimpleNamespace(
        id=task_id,
        project_id=project_id,
        orchestration_markers=markers,
    )


def _make_engine(project_svc: Any, task_svc: Any) -> tuple[DocsSyncEngine, list[Any]]:
    session = MagicMock()
    session.flush = AsyncMock(return_value=None)
    engine = DocsSyncEngine(session)
    patchers = [
        patch(
            "roboco.services.docs_sync_engine.get_project_service",
            return_value=project_svc,
        ),
        patch(
            "roboco.services.docs_sync_engine.get_task_service",
            return_value=task_svc,
        ),
    ]
    for p in patchers:
        p.start()
    return engine, patchers


@pytest.fixture
def _enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "docs_sync_enabled", True)
    monkeypatch.setattr(settings, "docs_sync_max_open_tasks", 3)
    monkeypatch.setattr(settings, "docs_sync_max_per_cycle", 1)


@pytest.mark.asyncio
async def test_enabled_opens_one_docs_update_task(_enabled: None) -> None:
    project_id = uuid4()
    project = _project(
        project_id, "roboco-website", "https://github.com/x/roboco-website.git"
    )
    created = _task(uuid4(), project_id, "0.23.0")

    project_svc = MagicMock()
    project_svc.get_by_slug = AsyncMock(return_value=project)
    task_svc = MagicMock()
    task_svc.list_open_docs_sync_tasks = AsyncMock(return_value=[])
    task_svc.create = AsyncMock(return_value=created)

    engine, patchers = _make_engine(project_svc, task_svc)
    try:
        result = await engine.originate_docs_update(
            version="0.23.0",
            changelog="## [0.23.0]\n\n### Added\n- docs-sync engine\n",
        )
    finally:
        for p in patchers:
            p.stop()

    assert result is not None
    assert result.id == created.id
    assert result.orchestration_markers is not None
    assert result.orchestration_markers.get("docs_sync_release_version") == "0.23.0"
    task_svc.create.assert_awaited_once()
    req = task_svc.create.await_args.args[0]
    assert req.project_id == project_id
    assert req.source == "docs_sync"
    assert "docs-sync engine" in req.description
    assert "Divergence checklist" in req.description


@pytest.mark.asyncio
async def test_same_version_is_deduped(_enabled: None) -> None:
    project_id = uuid4()
    project = _project(
        project_id, "roboco-website", "https://github.com/x/roboco-website.git"
    )
    open_task = _task(uuid4(), project_id, "0.23.0")

    project_svc = MagicMock()
    project_svc.get_by_slug = AsyncMock(return_value=project)
    task_svc = MagicMock()
    task_svc.list_open_docs_sync_tasks = AsyncMock(return_value=[open_task])
    task_svc.create = AsyncMock()

    engine, patchers = _make_engine(project_svc, task_svc)
    try:
        result = await engine.originate_docs_update(version="0.23.0", changelog="x")
    finally:
        for p in patchers:
            p.stop()

    assert result is None
    task_svc.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_different_versions_open_distinct_tasks(_enabled: None) -> None:
    project_id = uuid4()
    project = _project(
        project_id, "roboco-website", "https://github.com/x/roboco-website.git"
    )
    open_task = _task(uuid4(), project_id, "0.23.0")
    new_task = _task(uuid4(), project_id, "0.24.0")

    project_svc = MagicMock()
    project_svc.get_by_slug = AsyncMock(return_value=project)
    task_svc = MagicMock()
    task_svc.list_open_docs_sync_tasks = AsyncMock(
        side_effect=lambda version=None: (
            [open_task]
            if version == "0.23.0"
            else ([] if version == "0.24.0" else [open_task])
        )
    )
    task_svc.create = AsyncMock(return_value=new_task)

    engine, patchers = _make_engine(project_svc, task_svc)
    try:
        result = await engine.originate_docs_update(version="0.24.0", changelog="y")
    finally:
        for p in patchers:
            p.stop()

    assert result is not None
    assert result.id == new_task.id
    task_svc.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "docs_sync_enabled", False)
    project_svc = MagicMock()
    project_svc.get_by_slug = AsyncMock()
    task_svc = MagicMock()
    task_svc.create = AsyncMock()

    engine, patchers = _make_engine(project_svc, task_svc)
    try:
        result = await engine.originate_docs_update(version="0.23.0", changelog="x")
    finally:
        for p in patchers:
            p.stop()

    assert result is None
    assert project_svc.get_by_slug.await_count == 0
    assert task_svc.create.await_count == 0


@pytest.mark.asyncio
async def test_missing_project_warns_and_returns_none(
    _enabled: None, caplog: pytest.LogCaptureFixture
) -> None:
    project_svc = MagicMock()
    project_svc.get_by_slug = AsyncMock(return_value=None)
    task_svc = MagicMock()
    task_svc.create = AsyncMock()

    engine, patchers = _make_engine(project_svc, task_svc)
    try:
        with caplog.at_level("WARNING", logger="roboco.services.docs_sync_engine"):
            result = await engine.originate_docs_update(version="0.23.0", changelog="x")
    finally:
        for p in patchers:
            p.stop()

    assert result is None
    assert "roboco-website" in caplog.text
    assert "not registered" in caplog.text
    assert task_svc.create.await_count == 0


@pytest.mark.asyncio
async def test_open_task_cap_is_enforced(
    _enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "docs_sync_max_open_tasks", 1)
    project_id = uuid4()
    project = _project(
        project_id, "roboco-website", "https://github.com/x/roboco-website.git"
    )
    open_task = _task(uuid4(), project_id, "0.23.0")

    project_svc = MagicMock()
    project_svc.get_by_slug = AsyncMock(return_value=project)
    task_svc = MagicMock()
    task_svc.list_open_docs_sync_tasks = AsyncMock(return_value=[open_task])
    task_svc.create = AsyncMock()

    engine, patchers = _make_engine(project_svc, task_svc)
    try:
        result = await engine.originate_docs_update(version="0.24.0", changelog="y")
    finally:
        for p in patchers:
            p.stop()

    assert result is None
    task_svc.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_per_cycle_cap_is_enforced(
    _enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the per-cycle cap is reached, further calls on the same engine no-op."""
    monkeypatch.setattr(settings, "docs_sync_max_per_cycle", 1)
    project_id = uuid4()
    project = _project(
        project_id, "roboco-website", "https://github.com/x/roboco-website.git"
    )
    first_task = _task(uuid4(), project_id, "0.23.0")
    second_task = _task(uuid4(), project_id, "0.24.0")

    project_svc = MagicMock()
    project_svc.get_by_slug = AsyncMock(return_value=project)
    task_svc = MagicMock()
    task_svc.list_open_docs_sync_tasks = AsyncMock(
        side_effect=lambda version=None: [first_task] if version is None else []
    )
    task_svc.create = AsyncMock(side_effect=[first_task, second_task])

    engine, patchers = _make_engine(project_svc, task_svc)
    try:
        first = await engine.originate_docs_update(version="0.23.0", changelog="x")
        second = await engine.originate_docs_update(version="0.24.0", changelog="y")
    finally:
        for p in patchers:
            p.stop()

    assert first is not None
    assert first.id == first_task.id
    assert second is None
    task_svc.create.assert_awaited_once()

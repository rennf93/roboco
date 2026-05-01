"""Unit tests for WorkSessionService gateway-backfill methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.work_session import WorkSessionService


def _service() -> WorkSessionService:
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    return WorkSessionService(session)


def _bind(svc: WorkSessionService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


@pytest.mark.asyncio
async def test_files_changed_returns_files_modified_list() -> None:
    svc = _service()
    fake_session = MagicMock(files_modified=["roboco/api/app.py", "README.md"])
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    out = await svc.files_changed(uuid4())
    assert out == ["roboco/api/app.py", "README.md"]


@pytest.mark.asyncio
async def test_files_changed_returns_empty_list_when_session_missing() -> None:
    svc = _service()
    _bind(svc, "get", AsyncMock(return_value=None))
    assert await svc.files_changed(uuid4()) == []


@pytest.mark.asyncio
async def test_files_changed_handles_none_files_modified() -> None:
    svc = _service()
    fake_session = MagicMock(files_modified=None)
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    assert await svc.files_changed(uuid4()) == []


@pytest.mark.asyncio
async def test_has_unpushed_commits_false_when_no_commits() -> None:
    svc = _service()
    fake_session = MagicMock(commits=[], pr_number=None)
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    assert await svc.has_unpushed_commits(uuid4()) is False


@pytest.mark.asyncio
async def test_has_unpushed_commits_true_when_commits_but_no_pr() -> None:
    svc = _service()
    fake_session = MagicMock(commits=["abc", "def"], pr_number=None)
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    assert await svc.has_unpushed_commits(uuid4()) is True


@pytest.mark.asyncio
async def test_has_unpushed_commits_false_when_pr_exists() -> None:
    svc = _service()
    fake_session = MagicMock(commits=["abc"], pr_number=42)
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    assert await svc.has_unpushed_commits(uuid4()) is False


@pytest.mark.asyncio
async def test_has_unpushed_commits_false_when_session_missing() -> None:
    svc = _service()
    _bind(svc, "get", AsyncMock(return_value=None))
    assert await svc.has_unpushed_commits(uuid4()) is False

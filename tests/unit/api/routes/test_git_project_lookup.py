"""Unit tests: _resolve_project_slug accepts slug or UUID."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from roboco.api.routes.git import _resolve_project_slug

_HTTP_404 = 404


def _make_project(slug: str, uid: UUID) -> MagicMock:
    """Return a minimal project-like object."""
    project = MagicMock()
    project.slug = slug
    project.id = uid
    return project


@pytest.mark.asyncio
async def test_resolve_project_slug_accepts_slug() -> None:
    """A plain slug string resolves to the project's slug."""
    project = _make_project("roboco", uuid4())
    mock_service = MagicMock()
    mock_service.get_by_slug = AsyncMock(return_value=project)

    with patch("roboco.api.routes.git.get_project_service", return_value=mock_service):
        result = await _resolve_project_slug("roboco", MagicMock())

    assert result == "roboco"
    mock_service.get_by_slug.assert_awaited_once_with("roboco")
    mock_service.get.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_project_slug_accepts_uuid() -> None:
    """A UUID string resolves to the project's slug."""
    uid = uuid4()
    project = _make_project("roboco", uid)
    mock_service = MagicMock()
    mock_service.get = AsyncMock(return_value=project)

    with patch("roboco.api.routes.git.get_project_service", return_value=mock_service):
        result = await _resolve_project_slug(str(uid), MagicMock())

    assert result == "roboco"
    mock_service.get.assert_awaited_once_with(UUID(str(uid)))
    mock_service.get_by_slug.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_project_slug_raises_404_for_missing_slug() -> None:
    """Unknown slug raises HTTPException 404."""
    mock_service = MagicMock()
    mock_service.get_by_slug = AsyncMock(return_value=None)

    with (
        patch("roboco.api.routes.git.get_project_service", return_value=mock_service),
        pytest.raises(HTTPException) as exc_info,
    ):
        await _resolve_project_slug("nonexistent", MagicMock())

    assert exc_info.value.status_code == _HTTP_404
    assert "nonexistent" in exc_info.value.detail


@pytest.mark.asyncio
async def test_resolve_project_slug_raises_404_for_missing_uuid() -> None:
    """UUID that matches no project raises HTTPException 404."""
    uid = uuid4()
    mock_service = MagicMock()
    mock_service.get = AsyncMock(return_value=None)

    with (
        patch("roboco.api.routes.git.get_project_service", return_value=mock_service),
        pytest.raises(HTTPException) as exc_info,
    ):
        await _resolve_project_slug(str(uid), MagicMock())

    assert exc_info.value.status_code == _HTTP_404
    assert str(uid) in exc_info.value.detail

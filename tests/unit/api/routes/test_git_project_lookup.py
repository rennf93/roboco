"""Unit tests: ProjectService.resolve_slug_or_404 accepts slug or UUID."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from roboco.services.project import ProjectService

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
    service = ProjectService(MagicMock())
    service.get_by_slug = AsyncMock(return_value=project)  # type: ignore[method-assign]
    service.get = AsyncMock()  # type: ignore[method-assign]

    result = await service.resolve_slug_or_404("roboco")

    assert result == "roboco"
    service.get_by_slug.assert_awaited_once_with("roboco")
    service.get.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_project_slug_accepts_uuid() -> None:
    """A UUID string resolves to the project's slug."""
    uid = uuid4()
    project = _make_project("roboco", uid)
    service = ProjectService(MagicMock())
    service.get = AsyncMock(return_value=project)  # type: ignore[method-assign]
    service.get_by_slug = AsyncMock()  # type: ignore[method-assign]

    result = await service.resolve_slug_or_404(str(uid))

    assert result == "roboco"
    service.get.assert_awaited_once_with(UUID(str(uid)))
    service.get_by_slug.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_project_slug_raises_404_for_missing_slug() -> None:
    """Unknown slug raises HTTPException 404."""
    service = ProjectService(MagicMock())
    service.get_by_slug = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(HTTPException) as exc_info:
        await service.resolve_slug_or_404("nonexistent")

    assert exc_info.value.status_code == _HTTP_404
    assert "nonexistent" in exc_info.value.detail


@pytest.mark.asyncio
async def test_resolve_project_slug_raises_404_for_missing_uuid() -> None:
    """UUID that matches no project raises HTTPException 404."""
    uid = uuid4()
    service = ProjectService(MagicMock())
    service.get = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(HTTPException) as exc_info:
        await service.resolve_slug_or_404(str(uid))

    assert exc_info.value.status_code == _HTTP_404
    assert str(uid) in exc_info.value.detail

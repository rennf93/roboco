"""The first-clone conventions scaffold hook: flag-gated, file-absent, once."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import roboco.services.workspace as ws_mod
from roboco.config import settings
from roboco.services.workspace import WorkspaceService

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _SpyConventions:
    def __init__(self) -> None:
        self.scaffolded: list[Any] = []

    async def scaffold(self, project: Any, *, workspace: Path) -> None:
        self.scaffolded.append((project, workspace))


def _install_spy(monkeypatch: pytest.MonkeyPatch) -> _SpyConventions:
    ws_mod._SCAFFOLD_ATTEMPTED.clear()
    spy = _SpyConventions()
    monkeypatch.setattr(
        "roboco.services.conventions.get_conventions_service", lambda _s: spy
    )
    return spy


async def test_scaffold_fires_when_flag_on_and_file_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    spy = _install_spy(monkeypatch)
    svc = WorkspaceService(AsyncMock())
    await svc._maybe_scaffold_conventions(object(), "proj-a", tmp_path)
    assert len(spy.scaffolded) == 1


async def test_no_scaffold_when_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", False)
    spy = _install_spy(monkeypatch)
    svc = WorkspaceService(AsyncMock())
    await svc._maybe_scaffold_conventions(object(), "proj-b", tmp_path)
    assert spy.scaffolded == []


async def test_no_scaffold_when_file_already_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    spy = _install_spy(monkeypatch)
    (tmp_path / ".roboco").mkdir()
    (tmp_path / ".roboco" / "conventions.yml").write_text("version: 1\n")
    svc = WorkspaceService(AsyncMock())
    await svc._maybe_scaffold_conventions(object(), "proj-c", tmp_path)
    assert spy.scaffolded == []


async def test_scaffold_attempted_once_per_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    spy = _install_spy(monkeypatch)
    svc = WorkspaceService(AsyncMock())
    await svc._maybe_scaffold_conventions(object(), "proj-d", tmp_path)
    await svc._maybe_scaffold_conventions(object(), "proj-d", tmp_path)
    assert len(spy.scaffolded) == 1

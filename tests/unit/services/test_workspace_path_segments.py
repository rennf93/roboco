"""``get_workspace_path`` rejects traversal-capable path components.

Slugs/teams are regex- or enum-validated at creation, but every workspace path
is built at this one chokepoint — pin the by-construction guard so a raw
``../`` / absolute / NUL segment can never place a workspace outside the root,
regardless of what upstream validation a future caller forgets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.models.base import Team
from roboco.services.workspace import WorkspaceError, WorkspaceService

if TYPE_CHECKING:
    from pathlib import Path


def _service(root: Path) -> WorkspaceService:
    svc = WorkspaceService(MagicMock())
    svc.session = AsyncMock()
    svc.root = root
    return svc


@pytest.mark.parametrize(
    "bad",
    ["", ".", "..", "../escape", "a/b", "a\\b", "bad\x00slug", "/etc"],
)
def test_rejects_unsafe_project_slug(tmp_path: Path, bad: str) -> None:
    svc = _service(tmp_path)
    with pytest.raises(WorkspaceError, match="unsafe project slug"):
        svc.get_workspace_path(bad, Team.BACKEND, "be-dev-1")


@pytest.mark.parametrize("bad", ["..", "back/end", ""])
def test_rejects_unsafe_team_string(tmp_path: Path, bad: str) -> None:
    svc = _service(tmp_path)
    with pytest.raises(WorkspaceError, match="unsafe team"):
        svc.get_workspace_path("guard-core", bad, "be-dev-1")


@pytest.mark.parametrize("bad", ["..", "../../be-dev-1", "be\x00dev"])
def test_rejects_unsafe_agent_slug(tmp_path: Path, bad: str) -> None:
    svc = _service(tmp_path)
    with pytest.raises(WorkspaceError, match="unsafe agent slug"):
        svc.get_workspace_path("guard-core", Team.BACKEND, bad)


def test_valid_segments_unchanged(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    path = svc.get_workspace_path("guard-core", Team.BACKEND, "be-dev-1")
    assert path == tmp_path / "guard-core" / "backend" / "be-dev-1"

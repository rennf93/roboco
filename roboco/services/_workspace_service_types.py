"""Structural type for the composed WorkspaceService, TYPE_CHECKING-only.

``WorkspaceService`` is composed from mixins (``roboco/services/workspace.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
WorkspaceService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``WorkspaceServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``WorkspaceSurface`` what ``WorkspaceService`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_workspace_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import subprocess
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.models.base import Team
    from roboco.services.git_relay import GitRelay


class WorkspaceProvisioningSurface(Protocol):
    """``WorkspaceProvisioning``'s members that other classes reach."""

    @staticmethod
    def _is_workspace_healthy(workspace: Path) -> bool: ...
    async def ensure_workspace(
        self,
        project_slug: str,
        agent_id: UUID | str,
        git_url: str | None = ...,
        default_branch: str = ...,
        force: bool = ...,
        *,
        skip_refresh: bool = ...,
    ) -> Path: ...


class WorkspaceReadClonesSurface(Protocol):
    """``WorkspaceReadClones``'s members that other classes reach."""

    @staticmethod
    def _clone_local_into(
        read_clone: Path, clone_dir: Path, timeout: float
    ) -> None: ...
    @staticmethod
    async def _probe_lockfile_on_clone(
        clone_dir: Path, command: str, lock_paths: list[str]
    ) -> bool: ...
    async def ensure_read_clone(
        self, project_slug: str, *, force: bool = ...
    ) -> Path: ...


class WorkspaceSurface(Protocol):
    """``WorkspaceService``'s members that other classes reach."""

    async def _origin_git(
        self, repo: Path, project_slug: str, args: list[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]: ...
    async def _relay_for(self, project_slug: str) -> GitRelay | None: ...
    @staticmethod
    async def _resolve_git_token(
        project_service: Any, project_slug: str, git_url: str
    ) -> str | None: ...
    def get_workspace_path(
        self, project_slug: str, team: Team | str, agent_slug: str
    ) -> Path: ...


class WorkspaceToolchainSurface(Protocol):
    """``WorkspaceToolchain``'s members that other classes reach."""

    async def install_dev_deps(self, workspace: Path) -> bool: ...


class WorkspaceWorktreesSurface(Protocol):
    """``WorkspaceWorktrees``'s members that other classes reach."""

    async def _fetch_branch_ref(
        self, clone_root: Path, branch: str, project_slug: str | None
    ) -> None: ...
    @staticmethod
    def _link_shared_venv(worktree: Path, clone_root: Path) -> None: ...
    @staticmethod
    def _worktree_git(
        clone_root: Path, args: list[str], check: bool = ...
    ) -> subprocess.CompletedProcess[str]: ...
    async def ensure_worktree(
        self, clone_root: Path, worktree: Path, branch: str, base: str
    ) -> None: ...


class WorkspaceServiceSelf(
    WorkspaceProvisioningSurface,
    WorkspaceReadClonesSurface,
    WorkspaceSurface,
    WorkspaceToolchainSurface,
    WorkspaceWorktreesSurface,
    Protocol,
):
    """Every cross-class member a moved method body reaches via ``self``:
    WorkspaceService's instance attributes here, each class's
    members on its surface."""

    _fetch_cache: dict[str, float]
    root: Path
    session: AsyncSession

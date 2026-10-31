"""Structural type for the composed GitService, TYPE_CHECKING-only.

``GitService`` is composed from mixins (``roboco/services/git.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
GitService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``GitServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``GitSurface`` what ``GitService`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_git_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    import subprocess
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable
    from roboco.services.git_origin import OriginContext
    from roboco.services.git_relay import GitRelay


class GitBranchesSurface(Protocol):
    """``GitBranches``'s members that other classes reach."""

    async def _protected_branches_for(
        self, project_slug: str | None
    ) -> frozenset[str]: ...


class GitCommitsSurface(Protocol):
    """``GitCommits``'s members that other classes reach."""

    async def _assert_task_owned_with_branch(
        self, task_id: UUID, agent_id: UUID
    ) -> TaskTable: ...
    async def _ensure_worktree_for_commit(
        self, clone_root: Path, worktree: Path, branch: str | None
    ) -> None: ...
    @staticmethod
    def _worktree_for_task(clone_root: Path, task_id: UUID) -> Path: ...


class GitSurface(Protocol):
    """``GitService``'s members that other classes reach."""

    async def _origin_context(self, repo: Path, timeout: int) -> OriginContext: ...
    async def _project_for_task(self, task: Any) -> Any | None: ...
    async def _ref_exists(self, workspace: Any, ref: str) -> bool: ...
    async def _relay_for_repo(self, repo: Path) -> GitRelay: ...
    async def _run_codegen_and_commit(
        self, branch_name: str, workspace: Path, *, actor_agent_id: UUID | None = ...
    ) -> None: ...
    async def _run_git(
        self,
        workspace: Path,
        args: list[str],
        check: bool = ...,
        timeout: int | None = ...,
    ) -> subprocess.CompletedProcess[str]: ...
    async def get_workspace(
        self,
        project_slug: str,
        agent_id: UUID | None = ...,
        *,
        skip_refresh: bool = ...,
    ) -> Path: ...


class GitStatusSurface(Protocol):
    """``GitStatus``'s members that other classes reach."""

    async def get_current_branch(self, workspace: Path) -> str: ...
    async def get_status(
        self, workspace: Path
    ) -> tuple[str, bool, list[str], list[str], list[str], int, int]: ...


class GitServiceSelf(
    GitBranchesSurface, GitCommitsSurface, GitSurface, GitStatusSurface, Protocol
):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and GitService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]

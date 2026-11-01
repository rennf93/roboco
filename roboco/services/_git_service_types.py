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

    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.api.schemas.git import GitCreatePRRequest
    from roboco.db.tables import TaskTable
    from roboco.services.forge import GitProvider, RepoRef
    from roboco.services.git_origin import OriginContext
    from roboco.services.git_relay import GitRelay


class GitAgentWorkspaceSurface(Protocol):
    """``GitAgentWorkspace``'s members that other classes reach."""

    async def _ensure_base_on_remote(
        self, workspace: Path, base_branch: str, project_slug: str
    ) -> str: ...
    @staticmethod
    def _resolve_workspace_agent_id(
        task: Any, actor_agent_id: UUID | None
    ) -> UUID | None: ...
    async def _workspace_for_branch(
        self, branch_name: str, *, actor_agent_id: UUID | None = ...
    ) -> Path: ...


class GitBranchCleanupSurface(Protocol):
    """``GitBranchCleanup``'s members that other classes reach."""

    async def _delete_pr_branch_best_effort(
        self,
        repo_ref: RepoRef,
        pr_number: int,
        git_token: str,
        project_slug: str | None = ...,
    ) -> None: ...
    async def _sync_target_branch(self, workspace: Path, target_branch: str) -> str: ...
    async def _sync_target_branch_best_effort(
        self, workspace: Path, target_branch: str
    ) -> str | None: ...


class GitBranchesSurface(Protocol):
    """``GitBranches``'s members that other classes reach."""

    async def _project_default_branch(self, project_slug: str) -> str: ...
    async def _protected_branches_for(
        self, project_slug: str | None
    ) -> frozenset[str]: ...
    async def _protected_branches_for_deletion(
        self, project_slug: str | None
    ) -> frozenset[str] | None: ...


class GitCheckoutPushSurface(Protocol):
    """``GitCheckoutPush``'s members that other classes reach."""

    async def checkout(self, workspace: Path, branch: str) -> None: ...
    async def push(
        self, workspace: Path, force: bool = ..., branch: str | None = ...
    ) -> tuple[str, int]: ...


class GitCommitsSurface(Protocol):
    """``GitCommits``'s members that other classes reach."""

    async def _assert_on_task_branch(
        self, workspace: Path, task_branch: str | None
    ) -> None: ...
    async def _assert_task_owned_with_branch(
        self, task_id: UUID, agent_id: UUID
    ) -> TaskTable: ...
    async def _ensure_worktree_for_commit(
        self, clone_root: Path, worktree: Path, branch: str | None
    ) -> None: ...
    async def ensure_task_worktree(
        self, project_slug: str, agent_id: UUID, task: Any
    ) -> Path: ...
    async def _link_commit_to_task(
        self, task_uuid: UUID, commit_hash: str, message: str, agent_id: UUID
    ) -> None: ...
    def _parse_commit_stats(self, stat_output: str) -> tuple[int, int, int]: ...
    @staticmethod
    def _parse_git_url(url: str) -> RepoRef: ...
    @staticmethod
    def _worktree_for_task(clone_root: Path, task_id: UUID) -> Path: ...


class GitDiffsSurface(Protocol):
    """``GitDiffs``'s members that other classes reach."""

    async def _ref_exists(self, workspace: Any, ref: str) -> bool: ...
    async def list_changed_files(
        self,
        *,
        branch_name: str,
        base: str | None = ...,
        actor_agent_id: UUID | None = ...,
        preferred_parent: str | None = ...,
    ) -> list[str]: ...


class GitGatewayPRsSurface(Protocol):
    """``GitGatewayPRs``'s members that other classes reach."""

    async def _get_pr_refs(
        self, repo_ref: RepoRef, pr_number: int, git_token: str
    ) -> tuple[str, str] | None: ...
    async def _pr_is_merged(
        self, repo_ref: RepoRef, pr_number: int, git_token: str
    ) -> bool | None: ...


class GitMergeSurface(Protocol):
    """``GitMerge``'s members that other classes reach."""

    async def _call_merge_api(
        self, repo_ref: RepoRef, pr_number: int, git_token: str, merge_method: str
    ) -> httpx.Response: ...
    async def _first_allowed_merge_method(
        self, repo_ref: RepoRef, git_token: str, *, exclude: str | None = ...
    ) -> str | None: ...


class GitPRContextSurface(Protocol):
    """``GitPRContext``'s members that other classes reach."""

    async def _find_existing_pr(
        self, repo_ref: RepoRef, source_branch: str, target_branch: str, git_token: str
    ) -> dict[str, Any] | None: ...
    async def _generate_pr_title_body(
        self,
        request: GitCreatePRRequest,
        task: Any,
        source_branch: str,
        target_branch: str,
        task_id: UUID,
    ) -> tuple[str | None, str | None]: ...
    async def _get_project_token_or_raise(self, project_slug: str) -> str: ...
    async def _resolve_pr_target_branch(
        self, request: GitCreatePRRequest, task: Any, default_branch: str
    ) -> str: ...
    async def resolve_repo_and_token(
        self, project_slug: str
    ) -> tuple[RepoRef, str] | None: ...


class GitPRCreationSurface(Protocol):
    """``GitPRCreation``'s members that other classes reach."""

    async def _adopt_entire_pr(
        self,
        repo_ref: RepoRef,
        git_token: str,
        found: dict[str, Any],
        *,
        title_body: tuple[str, str],
        project: Any,
    ) -> None: ...
    async def _apply_pr_labels(
        self, repo_ref: RepoRef, git_token: str, pr_number: int, labels: list[str]
    ) -> None: ...
    async def _patch_pr_title_body(
        self,
        repo_ref: RepoRef,
        pr_number: int,
        git_token: str,
        payload: dict[str, str],
        *,
        current_body: str | None = ...,
    ) -> None: ...
    async def _post_pr(
        self, repo_ref: RepoRef, git_token: str, payload: dict[str, Any]
    ) -> httpx.Response: ...
    async def create_pull_request(
        self, workspace: Path, request: GitCreatePRRequest
    ) -> tuple[int, str, str, str, str]: ...


class GitPRReviewAndCISurface(Protocol):
    """``GitPRReviewAndCI``'s members that other classes reach."""

    async def _post_pr_reviewers(
        self, repo_ref: RepoRef, pr_number: int, git_token: str, reviewers: list[str]
    ) -> None: ...


class GitQualityGateSurface(Protocol):
    """``GitQualityGate``'s members that other classes reach."""

    async def _project_for_task(self, task: Any) -> Any | None: ...
    async def _run_codegen_and_commit(
        self, branch_name: str, workspace: Path, *, actor_agent_id: UUID | None = ...
    ) -> None: ...
    async def _task_for_branch(self, branch_name: str) -> TaskTable | None: ...


class GitRebaseSurface(Protocol):
    """``GitRebase``'s members that other classes reach."""

    async def _rev_list_count(self, workspace: Path, range_spec: str) -> int: ...


class GitSurface(Protocol):
    """``GitService``'s members that other classes reach."""

    @property
    def _forge(self) -> GitProvider: ...
    async def _origin_context(self, repo: Path, timeout: int) -> OriginContext: ...
    async def _relay_for_repo(self, repo: Path) -> GitRelay: ...
    async def _repo_ref_for_workspace(self, workspace: Path) -> RepoRef: ...
    async def _run_git(
        self,
        workspace: Path,
        args: list[str],
        check: bool = ...,
        timeout: int | None = ...,
    ) -> subprocess.CompletedProcess[str]: ...
    async def _token_for_project(self, project_slug: str) -> str | None: ...
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


class GitTaskPRsSurface(Protocol):
    """``GitTaskPRs``'s members that other classes reach."""

    async def _record_pr_atomically(
        self,
        task_uuid: UUID,
        pr_number: int,
        pr_url: str,
        audit_agent_id: UUID | None = ...,
    ) -> None: ...
    @staticmethod
    def _status_value(task: Any) -> str: ...


class GitServiceSelf(
    GitAgentWorkspaceSurface,
    GitBranchCleanupSurface,
    GitBranchesSurface,
    GitCheckoutPushSurface,
    GitCommitsSurface,
    GitDiffsSurface,
    GitGatewayPRsSurface,
    GitMergeSurface,
    GitPRContextSurface,
    GitPRCreationSurface,
    GitPRReviewAndCISurface,
    GitQualityGateSurface,
    GitRebaseSurface,
    GitSurface,
    GitStatusSurface,
    GitTaskPRsSurface,
    Protocol,
):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and GitService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]

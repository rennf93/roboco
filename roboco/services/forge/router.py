"""Per-call forge routing — the seam that lets ``GitService`` stay
provider-blind.

``GitService._forge`` returns a :class:`ForgeRouter`; every call site keeps
its exact Phase-1 shape (``self._forge.method(RepoRef(...), token, ...)``)
and the router picks the concrete transport per call from ``RepoRef.host``:
``None`` (or a github-registered host) → :class:`GitHubProvider`; a host
registered as gitea → :class:`GiteaProvider` for that host. The
host↔provider map is populated by ``registry.register_project_forge`` at the
project/token chokepoints — in-memory, per-process, self-healing on the next
project read after a restart.

``parse_repo_ref`` is the entry that stamps ``host`` onto the ref: GitHub
URLs parse exactly as before (host None), a registered gitea host parses
through its own provider, and an unregistered non-GitHub host fails loud
naming the fix instead of several steps deep.

Every transport method is an explicit one-line delegate (no ``__getattr__``
magic) so the ABC contract and mypy keep checking call sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.exceptions import GitError
from roboco.services.forge import registry
from roboco.services.forge.base import GitProvider, RepoRef
from roboco.services.forge.gitea import GiteaProvider
from roboco.services.forge.github import _REPO_URL_RE, GitHubProvider
from roboco.services.forge.gitlab import GitLabProvider

if TYPE_CHECKING:
    import httpx


class ForgeRouter(GitProvider):
    """Implements the :class:`GitProvider` surface by per-call delegation."""

    @staticmethod
    def _provider_for_ref(ref: RepoRef) -> GitProvider:
        if ref.host is None:
            return GitHubProvider()
        provider_name = registry.provider_name_for_host(ref.host)
        if provider_name == "gitea":
            return GiteaProvider(ref.host, scheme=registry.scheme_for_host(ref.host))
        if provider_name == "gitlab":
            return GitLabProvider(ref.host, scheme=registry.scheme_for_host(ref.host))
        if provider_name in (None, "github"):
            # A GHE host registered as github (or parsed before registration)
            # rides the GitHub transport with its configured base URL.
            return GitHubProvider()
        raise GitError(
            f"Unsupported git_provider {provider_name!r} for host {ref.host!r}.",
            {"git_provider": provider_name, "host": ref.host},
        )

    def parse_repo_ref(self, git_url: str) -> RepoRef:
        if _REPO_URL_RE.search(git_url):
            return GitHubProvider().parse_repo_ref(git_url)
        host = registry.host_of(git_url)
        provider_name = registry.provider_name_for_host(host) if host else None
        if host is not None and provider_name == "gitea":
            return GiteaProvider(
                host, scheme=registry.scheme_for_host(host)
            ).parse_repo_ref(git_url)
        if host is not None and provider_name == "gitlab":
            return GitLabProvider(
                host, scheme=registry.scheme_for_host(host)
            ).parse_repo_ref(git_url)
        raise GitError(
            "Could not resolve a forge for this remote URL — a non-GitHub "
            "host must belong to a registered project with git_provider set "
            "(gitea/gitlab today; GHE uses git_provider='github' with "
            "ROBOCO_GITHUB_API_BASE_URL).",
            {"host": host or "unknown"},
        )

    async def list_pulls(self, repo: RepoRef, token: str, **kwargs: Any) -> Any:
        return await self._provider_for_ref(repo).list_pulls(repo, token, **kwargs)

    async def get_pr(
        self, repo: RepoRef, token: str, pr_number: int, **kwargs: Any
    ) -> Any:
        return await self._provider_for_ref(repo).get_pr(
            repo, token, pr_number, **kwargs
        )

    async def get_pr_diff(self, repo: RepoRef, token: str, pr_number: int) -> Any:
        return await self._provider_for_ref(repo).get_pr_diff(repo, token, pr_number)

    async def create_pr(self, repo: RepoRef, token: str, **kwargs: Any) -> Any:
        return await self._provider_for_ref(repo).create_pr(repo, token, **kwargs)

    async def update_pr(
        self, repo: RepoRef, token: str, pr_number: int, **kwargs: Any
    ) -> Any:
        return await self._provider_for_ref(repo).update_pr(
            repo, token, pr_number, **kwargs
        )

    async def merge_pr(
        self, repo: RepoRef, token: str, pr_number: int, **kwargs: Any
    ) -> Any:
        return await self._provider_for_ref(repo).merge_pr(
            repo, token, pr_number, **kwargs
        )

    async def request_reviewers(
        self, repo: RepoRef, token: str, pr_number: int, reviewers: list[str]
    ) -> Any:
        return await self._provider_for_ref(repo).request_reviewers(
            repo, token, pr_number, reviewers
        )

    async def post_review(
        self, repo: RepoRef, token: str, pr_number: int, **kwargs: Any
    ) -> Any:
        return await self._provider_for_ref(repo).post_review(
            repo, token, pr_number, **kwargs
        )

    async def merge_branch(self, repo: RepoRef, token: str, **kwargs: Any) -> Any:
        return await self._provider_for_ref(repo).merge_branch(repo, token, **kwargs)

    async def list_ci_runs(self, repo: RepoRef, token: str, **kwargs: Any) -> Any:
        return await self._provider_for_ref(repo).list_ci_runs(repo, token, **kwargs)

    async def list_check_runs(
        self, repo: RepoRef, token: str, head_sha: str, **kwargs: Any
    ) -> Any:
        return await self._provider_for_ref(repo).list_check_runs(
            repo, token, head_sha, **kwargs
        )

    async def list_workflows(self, repo: RepoRef, token: str, **kwargs: Any) -> Any:
        return await self._provider_for_ref(repo).list_workflows(repo, token, **kwargs)

    async def get_repo(self, repo: RepoRef, token: str, **kwargs: Any) -> Any:
        return await self._provider_for_ref(repo).get_repo(repo, token, **kwargs)

    async def ensure_label(
        self, repo: RepoRef, token: str, name: str, color: str
    ) -> Any:
        return await self._provider_for_ref(repo).ensure_label(repo, token, name, color)

    async def add_labels(
        self, repo: RepoRef, token: str, pr_number: int, labels: list[str]
    ) -> Any:
        return await self._provider_for_ref(repo).add_labels(
            repo, token, pr_number, labels
        )

    async def delete_branch_ref(
        self, repo: RepoRef, token: str, branch: str, **kwargs: Any
    ) -> Any:
        return await self._provider_for_ref(repo).delete_branch_ref(
            repo, token, branch, **kwargs
        )

    async def create_issue_comment(
        self, repo: RepoRef, token: str, issue_number: int, body: str
    ) -> Any:
        return await self._provider_for_ref(repo).create_issue_comment(
            repo, token, issue_number, body
        )

    async def create_release(self, repo: RepoRef, token: str, **kwargs: Any) -> Any:
        return await self._provider_for_ref(repo).create_release(repo, token, **kwargs)

    async def create_org_repo(
        self,
        token: str,
        org: str,
        *,
        name: str,
        description: str,
        private: bool,
        auto_init: bool,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
    ) -> Any:
        """The one transport method with no RepoRef — provisioning is
        GitHub-only today, matching its direct GitHubProvider construction
        elsewhere."""
        return await GitHubProvider().create_org_repo(
            token,
            org,
            name=name,
            description=description,
            private=private,
            auto_init=auto_init,
            client=client,
            timeout=timeout,
        )

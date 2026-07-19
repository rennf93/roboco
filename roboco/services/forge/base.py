"""Provider-agnostic git-forge contracts.

Pure module: no ``httpx``, no ``settings``, no ``roboco.services`` imports —
just the shapes a caller needs to talk to *some* forge (GitHub today; GitLab /
Gitea in later phases) without knowing which one. A future ``GitLabProvider``
is implemented by reading this file alone: every method below is the full
surface ``GitService`` calls, with a docstring naming the REST operation it
stands in for.

Methods return ``Any`` deliberately — Phase 1's ``GitHubProvider`` returns raw
``httpx.Response`` objects (so ``GitService`` keeps its existing
status-code/body classification unchanged), and a provider is free to return
its own natural shape as long as callers can read `.status_code` /
`.is_success` / `.text` / `.json()` off it (or, for git.py's existing call
sites, an ``httpx.Response``-compatible object). The contract intentionally
does not force every provider through one wire format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RepoRef:
    """Provider-opaque repository identity.

    GitHub and Gitea both address a repo as ``owner/repo`` — the two fields
    below. A future GitLab provider addresses a repo by a URL-encoded full
    namespace path (subgroups included) that doesn't decompose into a single
    owner segment; ``GitLabProvider.parse_repo_ref`` is free to pack that
    whole path into ``owner`` and leave ``repo`` empty, or however it needs —
    every other method only ever receives a ``RepoRef`` back from
    ``parse_repo_ref``/construction, never assumes its internal shape beyond
    what THIS provider put there.

    ``host`` carries the forge host for self-hosted providers (a Gitea
    instance's API base derives from it); ``None`` means "the default GitHub
    host" so every existing two-arg construction site keeps its meaning.
    """

    owner: str
    repo: str
    host: str | None = None


class GitProvider(ABC):
    """The forge REST operations ``GitService`` performs, minus the
    classification of what a response means — that stays in ``GitService``.

    Every method is transport only: build the request, send it, hand back the
    response (or let a transport-level error propagate). Deciding whether a
    404 means "doesn't exist" vs. "no CI configured", retrying a specific
    status code as part of a *business* policy, and translating a failure
    into a domain exception are all ``GitService``'s job, not the provider's.
    """

    @abstractmethod
    def parse_repo_ref(self, git_url: str) -> RepoRef:
        """Parse a remote URL (https/ssh/tokened) into a :class:`RepoRef`."""

    @abstractmethod
    async def list_pulls(
        self,
        repo: RepoRef,
        token: str,
        *,
        head: str | None = None,
        base: str | None = None,
        state: str = "open",
        per_page: int | None = None,
        include_api_version: bool = True,
        timeout: float | None = None,
    ) -> Any:
        """List/filter pull requests — ``GET .../pulls``."""

    @abstractmethod
    async def get_pr(
        self,
        repo: RepoRef,
        token: str,
        pr_number: int,
        *,
        include_api_version: bool = True,
        timeout: float | None = None,
    ) -> Any:
        """Fetch one pull request — ``GET .../pulls/{n}``."""

    @abstractmethod
    async def get_pr_diff(self, repo: RepoRef, token: str, pr_number: int) -> Any:
        """Fetch a pull request's raw unified diff."""

    @abstractmethod
    async def create_pr(
        self, repo: RepoRef, token: str, *, head: str, base: str, title: str, body: str
    ) -> Any:
        """Open a pull request — ``POST .../pulls``."""

    @abstractmethod
    async def update_pr(
        self, repo: RepoRef, token: str, pr_number: int, *, payload: dict[str, Any]
    ) -> Any:
        """Patch a pull request's title/body/state — ``PATCH .../pulls/{n}``."""

    @abstractmethod
    async def merge_pr(
        self, repo: RepoRef, token: str, pr_number: int, *, merge_method: str
    ) -> Any:
        """Merge a pull request — ``PUT .../pulls/{n}/merge``."""

    @abstractmethod
    async def request_reviewers(
        self, repo: RepoRef, token: str, pr_number: int, reviewers: list[str]
    ) -> Any:
        """Request reviewers on a pull request."""

    @abstractmethod
    async def post_review(
        self, repo: RepoRef, token: str, pr_number: int, *, body: str, event: str
    ) -> Any:
        """Post a review (approve / request-changes / comment) on a pull request."""

    @abstractmethod
    async def merge_branch(
        self, repo: RepoRef, token: str, *, base: str, head: str, commit_message: str
    ) -> Any:
        """Server-side merge one branch into another (the env-sync cascade)."""

    @abstractmethod
    async def list_ci_runs(
        self,
        repo: RepoRef,
        token: str,
        *,
        workflow: str | None,
        branch: str,
        head_sha: str | None,
        per_page: int,
    ) -> Any:
        """List completed CI runs for a branch, optionally scoped to one workflow."""

    @abstractmethod
    async def list_check_runs(
        self, repo: RepoRef, token: str, head_sha: str, *, per_page: int
    ) -> Any:
        """List check-runs for a commit SHA."""

    @abstractmethod
    async def list_workflows(self, repo: RepoRef, token: str, *, per_page: int) -> Any:
        """List a repo's configured CI workflows (used to detect "no CI at all")."""

    @abstractmethod
    async def get_repo(self, repo: RepoRef, token: str) -> Any:
        """Fetch repo metadata (used for merge-method settings and to confirm a
        just-created repo)."""

    @abstractmethod
    async def ensure_label(
        self, repo: RepoRef, token: str, name: str, color: str
    ) -> Any:
        """Create a repo label if missing."""

    @abstractmethod
    async def add_labels(
        self, repo: RepoRef, token: str, pr_number: int, labels: list[str]
    ) -> Any:
        """Attach labels to an already-open PR/issue."""

    @abstractmethod
    async def delete_branch_ref(
        self, repo: RepoRef, token: str, branch: str, *, timeout: float | None = None
    ) -> Any:
        """Delete a branch ref on the remote."""

    @abstractmethod
    async def create_issue_comment(
        self, repo: RepoRef, token: str, issue_number: int, body: str
    ) -> Any:
        """Post a comment on an issue/PR."""

    @abstractmethod
    async def create_release(
        self,
        repo: RepoRef,
        token: str,
        *,
        tag_name: str,
        name: str,
        body: str,
        target_commitish: str,
        timeout: float | None = None,
    ) -> Any:
        """Publish a release."""

    @abstractmethod
    async def create_org_repo(
        self,
        token: str,
        org: str,
        *,
        name: str,
        description: str,
        private: bool,
        auto_init: bool,
    ) -> Any:
        """Create a new repository under an org (provisioning)."""

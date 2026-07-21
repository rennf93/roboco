"""GitHub REST transport — the concrete :class:`GitProvider` for Phase 1.

Every method here is pure wire mechanics: build the URL/headers/payload for
one GitHub REST endpoint, send it, return the raw ``httpx.Response``. No
status-code classification, no retries-as-business-policy, no logging — that
all stays in ``GitService``, which is the only caller that knows what a 404 or
an "already exists" 422 actually MEANS for the operation it's doing.

The one exception is ``list_ci_runs``: GitHub's Actions API is genuinely
flaky under load, so a bounded retry-with-backoff on transient failures
(timeouts, 429/5xx) is transport resilience, not business policy — it moves
here wholesale, returning whatever the last attempt produced (success or not)
for ``GitService`` to classify exactly as before.

Two client lifecycles are served by the same shared ``_send`` helper:
``GitService`` wants a fresh, auto-closed ``httpx.AsyncClient`` per call (its
existing pattern — and what the test suite patches via
``roboco.services.git.httpx.AsyncClient``, which works here too since
``httpx`` is a single shared module object regardless of which file imports
it); the release publisher injects/reuses its own client. Passing
``client=`` selects the second mode.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, cast

import httpx

from roboco.config import settings
from roboco.exceptions import GitError
from roboco.services.forge.base import GitProvider, RepoRef

if TYPE_CHECKING:
    from collections.abc import Mapping

# GitHub REST API version pinned via header on every versioned call — mirrors
# git.py's prior inline headers exactly.
_API_VERSION = "2022-11-28"
_DEFAULT_ACCEPT = "application/vnd.github+json"

# Transient-failure retry policy for the CI-runs list — GitHub Actions is
# flaky enough under load that a single blip must not silently drop a
# self-heal/release-gate poll.
_CI_FETCH_ATTEMPTS = 3
_CI_FETCH_BACKOFF_SECONDS = 0.5
_CI_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# owner/repo out of any accepted GitHub remote form: tokened/plain https, ssh.
_REPO_URL_RE = re.compile(
    r"github\.com[:/]+(?P<owner>[^/]+)/(?P<repo>[^/\s]+?)(?:\.git)?$"
)


def _default_timeout() -> int:
    """Read fresh on every call (not cached) so a test-time settings patch
    of ``git_command_timeout_seconds`` is honored — mirrors git.py's own
    ``_default_git_timeout``."""
    return settings.git_command_timeout_seconds


def _settings_api_base() -> str:
    """Read fresh on every call so a test-time patch of
    ``settings.github_api_base_url`` is honored (e.g. the e2e harness's fake
    GitHub server)."""
    return settings.github_api_base_url.rstrip("/")


class GitHubProvider(GitProvider):
    """GitHub.com / GitHub Enterprise REST transport."""

    def __init__(self, *, base_url: str | None = None) -> None:
        # An explicit override wins over the live setting; otherwise every call
        # re-reads the setting fresh.
        self._base_url_override = base_url.rstrip("/") if base_url else None

    def _api_base(self) -> str:
        return self._base_url_override or _settings_api_base()

    def _repo_url(self, repo: RepoRef, *segments: str) -> str:
        base = f"{self._api_base()}/repos/{repo.owner}/{repo.repo}"
        return "/".join([base, *segments]) if segments else base

    @staticmethod
    def _headers(
        token: str, *, accept: str = _DEFAULT_ACCEPT, include_api_version: bool = True
    ) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {token}", "Accept": accept}
        if include_api_version:
            headers["X-GitHub-Api-Version"] = _API_VERSION
        return headers

    async def _send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Issue one REST call — a fresh auto-closed client, or a caller-
        injected one reused across calls (the provisioning/release lifecycle).
        """
        kwargs: dict[str, Any] = {"headers": headers}
        if json_body is not None:
            kwargs["json"] = json_body
        if params is not None:
            kwargs["params"] = params
        if client is not None:
            if timeout is not None:
                kwargs["timeout"] = timeout
            return cast("httpx.Response", await getattr(client, method)(url, **kwargs))
        owned_timeout = timeout if timeout is not None else _default_timeout()
        async with httpx.AsyncClient(timeout=owned_timeout) as owned_client:
            return cast(
                "httpx.Response", await getattr(owned_client, method)(url, **kwargs)
            )

    # -- identity ----------------------------------------------------------

    def parse_repo_ref(self, git_url: str) -> RepoRef:
        match = _REPO_URL_RE.search(git_url)
        if not match:
            raise GitError(
                "Could not parse GitHub owner/repo from remote URL",
                {
                    "url_host": git_url.rsplit("@", maxsplit=1)[-1].split(
                        "/", maxsplit=1
                    )[0]
                },
            )
        return RepoRef(match.group("owner"), match.group("repo"))

    # -- pull requests -------------------------------------------------------

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
    ) -> httpx.Response:
        params: dict[str, Any] = {"state": state}
        if head is not None:
            params["head"] = f"{repo.owner}:{head}"
        if base is not None:
            params["base"] = base
        if per_page is not None:
            params["per_page"] = per_page
        headers = self._headers(token, include_api_version=include_api_version)
        return await self._send(
            "get",
            self._repo_url(repo, "pulls"),
            headers=headers,
            params=params,
            timeout=timeout,
        )

    async def get_pr(
        self,
        repo: RepoRef,
        token: str,
        pr_number: int,
        *,
        include_api_version: bool = True,
        timeout: float | None = None,
    ) -> httpx.Response:
        headers = self._headers(token, include_api_version=include_api_version)
        return await self._send(
            "get",
            self._repo_url(repo, "pulls", str(pr_number)),
            headers=headers,
            timeout=timeout,
        )

    async def get_pr_diff(
        self, repo: RepoRef, token: str, pr_number: int
    ) -> httpx.Response:
        headers = self._headers(token, accept="application/vnd.github.v3.diff")
        return await self._send(
            "get", self._repo_url(repo, "pulls", str(pr_number)), headers=headers
        )

    async def create_pr(
        self, repo: RepoRef, token: str, *, head: str, base: str, title: str, body: str
    ) -> httpx.Response:
        headers = self._headers(token)
        payload = {"title": title, "body": body, "head": head, "base": base}
        return await self._send(
            "post", self._repo_url(repo, "pulls"), headers=headers, json_body=payload
        )

    async def update_pr(
        self, repo: RepoRef, token: str, pr_number: int, *, payload: dict[str, Any]
    ) -> httpx.Response:
        headers = self._headers(token)
        return await self._send(
            "patch",
            self._repo_url(repo, "pulls", str(pr_number)),
            headers=headers,
            json_body=payload,
        )

    async def merge_pr(
        self, repo: RepoRef, token: str, pr_number: int, *, merge_method: str
    ) -> httpx.Response:
        headers = self._headers(token)
        return await self._send(
            "put",
            self._repo_url(repo, "pulls", str(pr_number), "merge"),
            headers=headers,
            json_body={"merge_method": merge_method},
        )

    async def request_reviewers(
        self, repo: RepoRef, token: str, pr_number: int, reviewers: list[str]
    ) -> httpx.Response:
        headers = self._headers(token)
        return await self._send(
            "post",
            self._repo_url(repo, "pulls", str(pr_number), "requested_reviewers"),
            headers=headers,
            json_body={"reviewers": reviewers},
        )

    async def post_review(
        self, repo: RepoRef, token: str, pr_number: int, *, body: str, event: str
    ) -> httpx.Response:
        headers = self._headers(token)
        return await self._send(
            "post",
            self._repo_url(repo, "pulls", str(pr_number), "reviews"),
            headers=headers,
            json_body={"body": body, "event": event},
        )

    async def merge_branch(
        self, repo: RepoRef, token: str, *, base: str, head: str, commit_message: str
    ) -> httpx.Response:
        headers = self._headers(token)
        payload = {"base": base, "head": head, "commit_message": commit_message}
        return await self._send(
            "post", self._repo_url(repo, "merges"), headers=headers, json_body=payload
        )

    # -- CI ------------------------------------------------------------------

    async def list_ci_runs(
        self,
        repo: RepoRef,
        token: str,
        *,
        workflow: str | None,
        branch: str,
        head_sha: str | None,
        per_page: int,
    ) -> httpx.Response:
        actions_base = self._repo_url(repo, "actions")
        url = (
            f"{actions_base}/workflows/{workflow}/runs"
            if workflow
            else f"{actions_base}/runs"
        )
        headers = self._headers(token)
        params: dict[str, Any] = {
            "branch": branch,
            "status": "completed",
            "per_page": per_page,
        }
        if head_sha:
            params["head_sha"] = head_sha
        resp: httpx.Response | None = None
        for attempt in range(_CI_FETCH_ATTEMPTS):
            last = attempt + 1 == _CI_FETCH_ATTEMPTS
            try:
                resp = await self._send("get", url, headers=headers, params=params)
            except httpx.HTTPError:
                if last:
                    raise
                await asyncio.sleep(_CI_FETCH_BACKOFF_SECONDS * (attempt + 1))
                continue
            if resp.is_success or resp.status_code not in _CI_RETRYABLE_STATUS or last:
                return resp
            await asyncio.sleep(_CI_FETCH_BACKOFF_SECONDS * (attempt + 1))
        # _CI_FETCH_ATTEMPTS >= 1, so the loop above always returns or raises.
        raise AssertionError("list_ci_runs: retry loop exited without a result")

    async def list_check_runs(
        self, repo: RepoRef, token: str, head_sha: str, *, per_page: int
    ) -> httpx.Response:
        headers = self._headers(token)
        return await self._send(
            "get",
            self._repo_url(repo, "commits", head_sha, "check-runs"),
            headers=headers,
            params={"per_page": per_page},
        )

    async def list_workflows(
        self, repo: RepoRef, token: str, *, per_page: int
    ) -> httpx.Response:
        headers = self._headers(token)
        return await self._send(
            "get",
            self._repo_url(repo, "actions", "workflows"),
            headers=headers,
            params={"per_page": per_page},
        )

    # -- repo / labels / branches / releases ----------------------------------

    async def get_repo(
        self,
        repo: RepoRef,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        headers = self._headers(token)
        return await self._send(
            "get", self._repo_url(repo), headers=headers, client=client, timeout=timeout
        )

    async def ensure_label(
        self, repo: RepoRef, token: str, name: str, color: str
    ) -> httpx.Response:
        headers = self._headers(token)
        return await self._send(
            "post",
            self._repo_url(repo, "labels"),
            headers=headers,
            json_body={"name": name, "color": color},
        )

    async def add_labels(
        self, repo: RepoRef, token: str, pr_number: int, labels: list[str]
    ) -> httpx.Response:
        headers = self._headers(token)
        return await self._send(
            "post",
            self._repo_url(repo, "issues", str(pr_number), "labels"),
            headers=headers,
            json_body={"labels": labels},
        )

    async def delete_branch_ref(
        self, repo: RepoRef, token: str, branch: str, *, timeout: float | None = None
    ) -> httpx.Response:
        headers = self._headers(token)
        return await self._send(
            "delete",
            self._repo_url(repo, "git", "refs", "heads", branch),
            headers=headers,
            timeout=timeout,
        )

    async def create_issue_comment(
        self, repo: RepoRef, token: str, issue_number: int, body: str
    ) -> httpx.Response:
        headers = self._headers(token)
        return await self._send(
            "post",
            self._repo_url(repo, "issues", str(issue_number), "comments"),
            headers=headers,
            json_body={"body": body},
        )

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
    ) -> httpx.Response:
        headers = self._headers(token)
        payload = {
            "tag_name": tag_name,
            "name": name,
            "body": body,
            "target_commitish": target_commitish,
        }
        return await self._send(
            "post",
            self._repo_url(repo, "releases"),
            headers=headers,
            json_body=payload,
            timeout=timeout,
        )

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
    ) -> httpx.Response:
        headers = self._headers(token)
        payload = {
            "name": name,
            "description": description,
            "private": private,
            "auto_init": auto_init,
        }
        return await self._send(
            "post",
            f"{self._api_base()}/orgs/{org}/repos",
            headers=headers,
            json_body=payload,
            client=client,
            timeout=timeout,
        )

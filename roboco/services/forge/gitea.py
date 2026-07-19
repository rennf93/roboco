"""Gitea REST transport — Phase 2 of the forge-providers spec.

Gitea's API is deliberately GitHub-shaped, so most methods are the same
paths against ``https://{host}/api/v1`` with ``Authorization: token`` (the
scheme Gitea's classic PATs require; Bearer is rejected). Where Gitea's wire
contract diverges, this provider ADAPTS the response back into the GitHub
shape ``GitService`` classifies (see the per-method notes) rather than
teaching ``GitService`` a second dialect — the seam's contract is that
callers keep reading ``.status_code`` / ``.is_success`` / ``.text`` /
``.json()`` exactly as they do for GitHub. Adapted responses are wrapped in
:class:`ShapedResponse`.

Deliberate Phase-2 postures (per the spec):

- CI is classified from Gitea's commit-status API reshaped into GitHub's
  ``check_runs`` / ``workflow_runs`` envelopes; ``list_workflows`` always
  reports zero so a statuses-free repo classifies as ``no_ci_configured``
  (fail-open, the posture the GitHub path takes for unreachable repos).
- ``merge_branch`` (the env-sync cascade's server-side merge) has no Gitea
  equivalent; it returns a shaped 501 so ``_env_merge_status`` lands on its
  existing ``missing_ref`` branch. The shared local-git fallback is a
  follow-up, not silently faked here.
- Plain git (clone/fetch/push) needs no provider work: Gitea, like GitHub
  and GitLab, accepts a PAT as the Basic-auth password with the username
  ignored, so the existing ``x-access-token:<token>`` extraheader works.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

import httpx

from roboco.config import settings
from roboco.exceptions import GitError
from roboco.services.forge.base import GitProvider, RepoRef
from roboco.services.forge.shaping import ShapedResponse

if TYPE_CHECKING:
    from collections.abc import Mapping

# Gitea review-event vocabulary differs from GitHub's by one word.
_REVIEW_EVENT_MAP = {"APPROVE": "APPROVED"}

# Gitea commit-status states → GitHub check-run/workflow-run vocabulary.
_STATUS_CONCLUSION = {"success": "success", "failure": "failure", "error": "failure"}


def _default_timeout() -> int:
    return settings.git_command_timeout_seconds


class GiteaProvider(GitProvider):
    """Self-hosted Gitea transport, addressed by instance host.

    ``scheme`` comes from the project's git_url via the registry — a LAN
    instance serving plain http (no TLS terminator) is a real deployment
    shape, not just a test convenience.
    """

    def __init__(self, host: str, scheme: str = "https") -> None:
        self._host = host.strip().rstrip("/")
        self._scheme = scheme
        self._repo_url_re = re.compile(
            re.escape(self._host)
            + r"[:/]+(?P<owner>[^/]+)/(?P<repo>[^/\s]+?)(?:\.git)?$"
        )

    def _api_base(self) -> str:
        return f"{self._scheme}://{self._host}/api/v1"

    def _repo_url(self, repo: RepoRef, *segments: str) -> str:
        base = f"{self._api_base()}/repos/{repo.owner}/{repo.repo}"
        return "/".join([base, *segments]) if segments else base

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        # Gitea classic PATs require the `token` scheme; Bearer is rejected.
        return {"Authorization": f"token {token}", "Accept": "application/json"}

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
        match = self._repo_url_re.search(git_url)
        if not match:
            raise GitError(
                "Could not parse Gitea owner/repo from remote URL",
                {"host": self._host},
            )
        return RepoRef(match.group("owner"), match.group("repo"), host=self._host)

    # -- pull requests -----------------------------------------------------

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
        """Gitea's list endpoint has no head/base filters — fetch and filter
        client-side, reshaping each element with the one field GitHub has
        and Gitea lacks (``author_association``)."""
        _ = include_api_version
        params: dict[str, Any] = {"state": state, "limit": per_page or 50}
        resp = await self._send(
            "get",
            self._repo_url(repo, "pulls"),
            headers=self._headers(token),
            params=params,
            timeout=timeout,
        )
        if not resp.is_success:
            return resp
        pulls = resp.json()
        if not isinstance(pulls, list):
            return resp
        selected = [
            self._shape_pull(pr)
            for pr in pulls
            if self._pull_matches(pr, head=head, base=base)
        ]
        return ShapedResponse(resp, json_payload=selected)

    @staticmethod
    def _pull_matches(
        pr: dict[str, Any], *, head: str | None, base: str | None
    ) -> bool:
        if head is not None and ((pr.get("head") or {}).get("ref")) != head:
            return False
        return not (base is not None and ((pr.get("base") or {}).get("ref")) != base)

    @staticmethod
    def _shape_pull(pr: dict[str, Any]) -> dict[str, Any]:
        shaped = dict(pr)
        shaped.setdefault("author_association", "NONE")
        return shaped

    async def get_pr(
        self,
        repo: RepoRef,
        token: str,
        pr_number: int,
        *,
        include_api_version: bool = True,
        timeout: float | None = None,
    ) -> Any:
        _ = include_api_version
        return await self._send(
            "get",
            self._repo_url(repo, "pulls", str(pr_number)),
            headers=self._headers(token),
            timeout=timeout,
        )

    async def get_pr_diff(self, repo: RepoRef, token: str, pr_number: int) -> Any:
        return await self._send(
            "get",
            self._repo_url(repo, "pulls", f"{pr_number}.diff"),
            headers=self._headers(token),
        )

    async def create_pr(
        self, repo: RepoRef, token: str, *, head: str, base: str, title: str, body: str
    ) -> Any:
        """Gitea signals the duplicate-PR case as 409 where GitHub uses 422;
        reshape that one case so GitService's idempotency branch
        (`422 and "already exists" in text`) keeps working."""
        resp = await self._send(
            "post",
            self._repo_url(repo, "pulls"),
            headers=self._headers(token),
            json_body={"title": title, "body": body, "head": head, "base": base},
        )
        if resp.status_code == httpx.codes.CONFLICT and "already exists" in resp.text:
            return ShapedResponse(resp, status_code=httpx.codes.UNPROCESSABLE_ENTITY)
        return resp

    async def update_pr(
        self, repo: RepoRef, token: str, pr_number: int, *, payload: dict[str, Any]
    ) -> Any:
        return await self._send(
            "patch",
            self._repo_url(repo, "pulls", str(pr_number)),
            headers=self._headers(token),
            json_body=payload,
        )

    async def merge_pr(
        self, repo: RepoRef, token: str, pr_number: int, *, merge_method: str
    ) -> Any:
        # Gitea: POST (not PUT) with the method under "Do".
        return await self._send(
            "post",
            self._repo_url(repo, "pulls", str(pr_number), "merge"),
            headers=self._headers(token),
            json_body={"Do": merge_method},
        )

    async def request_reviewers(
        self, repo: RepoRef, token: str, pr_number: int, reviewers: list[str]
    ) -> Any:
        return await self._send(
            "post",
            self._repo_url(repo, "pulls", str(pr_number), "requested_reviewers"),
            headers=self._headers(token),
            json_body={"reviewers": reviewers},
        )

    async def post_review(
        self, repo: RepoRef, token: str, pr_number: int, *, body: str, event: str
    ) -> Any:
        return await self._send(
            "post",
            self._repo_url(repo, "pulls", str(pr_number), "reviews"),
            headers=self._headers(token),
            json_body={"body": body, "event": _REVIEW_EVENT_MAP.get(event, event)},
        )

    async def merge_branch(
        self, repo: RepoRef, token: str, *, base: str, head: str, commit_message: str
    ) -> Any:
        """No server-side merges API on Gitea — a shaped 501 lands
        ``_env_merge_status`` on its existing ``missing_ref`` branch."""
        _ = (repo, token, base, head, commit_message)
        request = httpx.Request("post", f"https://{self._host}/unsupported")
        real = httpx.Response(
            httpx.codes.NOT_IMPLEMENTED,
            request=request,
            json={"message": "Gitea has no server-side branch-merge API"},
        )
        return ShapedResponse(real)

    # -- CI ----------------------------------------------------------------

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
        """Gitea has no workflow-runs listing with GitHub's shape; the
        combined commit status for the branch head is the signal. A settled
        combined state becomes one synthetic ``workflow_runs`` entry (the
        consumer picks conclusion/head_sha off it); pending or empty yields
        no completed runs, exactly like GitHub's ``status=completed``
        filter."""
        _ = (workflow, head_sha, per_page)
        resp = await self._send(
            "get",
            # Branch names carry slashes (feature/backend/...) — encode or
            # Gitea's router 404s on the extra path segments.
            self._repo_url(repo, "commits", quote(branch, safe=""), "status"),
            headers=self._headers(token),
        )
        if not resp.is_success:
            return resp
        data = resp.json() if isinstance(resp.json(), dict) else {}
        conclusion = _STATUS_CONCLUSION.get(str(data.get("state") or "").lower())
        runs: list[dict[str, Any]] = []
        if conclusion is not None:
            runs.append(
                {
                    "head_sha": data.get("sha") or "",
                    "run_attempt": 1,
                    "conclusion": conclusion,
                    "name": "combined-status",
                    "html_url": data.get("url") or "",
                    "updated_at": "",
                }
            )
        return ShapedResponse(resp, json_payload={"workflow_runs": runs})

    async def list_check_runs(
        self, repo: RepoRef, token: str, head_sha: str, *, per_page: int
    ) -> Any:
        """Commit statuses reshaped into GitHub's ``check_runs`` envelope —
        the per-name latest-wins dedup upstream keys on ``id``, which Gitea
        statuses already increment."""
        resp = await self._send(
            "get",
            self._repo_url(repo, "commits", head_sha, "statuses"),
            headers=self._headers(token),
            params={"limit": per_page},
        )
        if not resp.is_success:
            return resp
        statuses = resp.json()
        if not isinstance(statuses, list):
            return resp
        check_runs = [self._shape_status(status) for status in statuses]
        return ShapedResponse(resp, json_payload={"check_runs": check_runs})

    @staticmethod
    def _shape_status(status: dict[str, Any]) -> dict[str, Any]:
        state = str(status.get("status") or "").lower()
        settled = state in _STATUS_CONCLUSION
        return {
            "id": status.get("id") or 0,
            "name": status.get("context") or "status",
            "status": "completed" if settled else "in_progress",
            "conclusion": _STATUS_CONCLUSION.get(state),
        }

    async def list_workflows(self, repo: RepoRef, token: str, *, per_page: int) -> Any:
        """Fail-open: no cheap "is CI configured at all" probe exists on
        Gitea, so zero-check-runs classifies as ``no_ci_configured`` (the
        spec's chosen posture) rather than ``pending_not_scheduled``."""
        _ = (repo, token, per_page)
        request = httpx.Request("get", f"https://{self._host}/synthetic")
        real = httpx.Response(httpx.codes.OK, request=request, json={"total_count": 0})
        return ShapedResponse(real)

    # -- repo / labels / branches / releases -------------------------------

    async def get_repo(
        self,
        repo: RepoRef,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Gitea names the merge-method toggles differently — reshape onto
        GitHub's ``allow_*`` keys the merge-method fallback reads."""
        resp = await self._send(
            "get",
            self._repo_url(repo),
            headers=self._headers(token),
            client=client,
            timeout=timeout,
        )
        if not resp.is_success:
            return resp
        data = resp.json()
        if not isinstance(data, dict):
            return resp
        shaped = dict(data)
        shaped["allow_merge_commit"] = data.get("allow_merge_commits", True)
        shaped["allow_rebase_merge"] = data.get("allow_rebase", True)
        shaped.setdefault("allow_squash_merge", True)
        return ShapedResponse(resp, json_payload=shaped)

    async def ensure_label(
        self, repo: RepoRef, token: str, name: str, color: str
    ) -> Any:
        # Gitea wants the leading '#' on label colors; GitHub omits it.
        hex_color = color if color.startswith("#") else f"#{color}"
        return await self._send(
            "post",
            self._repo_url(repo, "labels"),
            headers=self._headers(token),
            json_body={"name": name, "color": hex_color},
        )

    async def add_labels(
        self, repo: RepoRef, token: str, pr_number: int, labels: list[str]
    ) -> Any:
        return await self._send(
            "post",
            self._repo_url(repo, "issues", str(pr_number), "labels"),
            headers=self._headers(token),
            json_body={"labels": labels},
        )

    async def delete_branch_ref(
        self, repo: RepoRef, token: str, branch: str, *, timeout: float | None = None
    ) -> Any:
        return await self._send(
            "delete",
            self._repo_url(repo, "branches", quote(branch, safe="")),
            headers=self._headers(token),
            timeout=timeout,
        )

    async def create_issue_comment(
        self, repo: RepoRef, token: str, issue_number: int, body: str
    ) -> Any:
        return await self._send(
            "post",
            self._repo_url(repo, "issues", str(issue_number), "comments"),
            headers=self._headers(token),
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
    ) -> Any:
        return await self._send(
            "post",
            self._repo_url(repo, "releases"),
            headers=self._headers(token),
            json_body={
                "tag_name": tag_name,
                "name": name,
                "body": body,
                "target_commitish": target_commitish,
            },
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
    ) -> Any:
        return await self._send(
            "post",
            f"{self._api_base()}/orgs/{org}/repos",
            headers=self._headers(token),
            json_body={
                "name": name,
                "description": description,
                "private": private,
                "auto_init": auto_init,
            },
            client=client,
            timeout=timeout,
        )

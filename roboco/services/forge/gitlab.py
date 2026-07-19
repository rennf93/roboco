"""GitLab REST v4 transport — Phase 3 of the forge-providers spec.

GitLab's API is the most semantically divergent of the three forges: pull
requests are "merge requests" addressed by a per-project ``iid``, review has
no request-changes verb, CI is pipelines/statuses rather than
workflows/check-runs, and a repo is addressed by its full (URL-encoded)
namespace path rather than an ``owner/repo`` pair — subgroups make that path
arbitrarily deep, which is exactly what :class:`~roboco.services.forge.base.RepoRef`
was built to shrug off (the whole path packs into ``owner``; ``repo`` is
unused). Every method below ADAPTS the GitLab wire shape back into the GitHub
shape ``GitService`` classifies (see the per-method notes), wrapped in
:class:`ShapedResponse` — callers keep reading ``.status_code`` /
``.is_success`` / ``.text`` / ``.json()`` exactly as they do for GitHub.

Deliberate Phase-3 postures (per the spec):

- ``merge_branch`` (the env-sync cascade's server-side merge) has no GitLab
  equivalent; it returns a shaped 501 so ``_env_merge_status`` lands on its
  existing ``missing_ref`` branch, same as Gitea.
- ``request_reviewers`` needs numeric GitLab user ids RoboCo does not store
  (only usernames/slugs) — mirroring is skipped with a synthetic 200 rather
  than failing the PR-open flow over a best-effort reviewer nudge.
- ``create_org_repo`` (provisioning, Phase 4) resolves ``org`` — a group's
  full path, subgroups included — to a numeric namespace id via ``GET
  /groups/{path}`` and POSTs ``/projects`` under it; a 404 group lookup
  falls back to the token's own (user) namespace by omitting
  ``namespace_id``.
- Plain git (clone/fetch/push) needs no provider work: GitLab, like GitHub
  and Gitea, accepts a PAT as the Basic-auth password with the username
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

# GitLab MR list `state` param: "open" is GitHub/RoboCo's vocabulary, GitLab
# calls it "opened". Every other value (closed/merged/locked/all) passes
# through unchanged.
_STATE_PARAM_MAP = {"open": "opened"}

# GitLab pipeline/commit-status states → GitHub check-run/workflow-run
# conclusion vocabulary. Unsettled states (running/pending/created) are
# absent on purpose — they classify as "not yet concluded" by omission.
_CONCLUSION_MAP = {"success": "success", "failed": "failure", "canceled": "failure"}
_IN_PROGRESS_STATUSES = frozenset({"running", "pending", "created"})

# get_pr_diff pagination cap — GitLab's /diffs endpoint is JSON-per-file
# with no raw-unified-diff media type, so a large MR needs multiple pages
# reassembled; bounded so one pathological MR can't loop forever.
_DIFF_MAX_PAGES = 3
_DIFF_PAGE_SIZE = 100

# A GitLab project path is at least "namespace/project" — one bare segment
# can't be a valid remote (no owner-less repos on GitLab).
_MIN_PATH_SEGMENTS = 2

# GitLab project ``path`` charset is stricter than a display ``name`` —
# lowercase alnum/dot/underscore/dash. RoboCo repo names are already slugs
# (pitch.slug-derived) so this is normally a no-op.
_PATH_INVALID_CHARS_RE = re.compile(r"[^a-z0-9._-]+")


def _default_timeout() -> int:
    return settings.git_command_timeout_seconds


def _project_path(name: str) -> str:
    slug = _PATH_INVALID_CHARS_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "project"


class GitLabProvider(GitProvider):
    """Self-hosted or gitlab.com REST v4 transport, addressed by instance host."""

    def __init__(self, host: str, scheme: str = "https") -> None:
        self._host = host.strip().rstrip("/")
        self._scheme = scheme
        self._repo_url_re = re.compile(
            re.escape(self._host) + r"[:/]+(?P<path>[^\s]+?)(?:\.git)?$"
        )

    def _project_base(self, repo: RepoRef) -> str:
        # RepoRef.owner carries the FULL namespace path (subgroups included);
        # GitLab addresses a project by that path (or numeric id) URL-encoded
        # as a single path segment.
        encoded = quote(repo.owner, safe="")
        return f"{self._scheme}://{self._host}/api/v4/projects/{encoded}"

    def _url(self, repo: RepoRef, *segments: str) -> str:
        base = self._project_base(repo)
        return "/".join([base, *segments]) if segments else base

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

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

    # -- identity ------------------------------------------------------------

    def parse_repo_ref(self, git_url: str) -> RepoRef:
        """Pack the FULL namespace path (subgroups allowed, 2+ segments)
        into ``RepoRef.owner``; ``repo`` stays empty since GitLab addresses
        a project by that whole path, not an owner/repo pair."""
        match = self._repo_url_re.search(git_url)
        if not match:
            raise GitError(
                "Could not parse GitLab project path from remote URL",
                {"host": self._host},
            )
        segments = [s for s in match.group("path").strip("/").split("/") if s]
        if len(segments) < _MIN_PATH_SEGMENTS:
            raise GitError(
                "GitLab remote URL is missing a namespace/project path",
                {"host": self._host, "path": match.group("path")},
            )
        return RepoRef("/".join(segments), "", host=self._host)

    # -- pull requests (merge requests) --------------------------------------

    @staticmethod
    def _shape_pull(mr: dict[str, Any], repo: RepoRef) -> dict[str, Any]:
        gitlab_state = mr.get("state")
        author = mr.get("author") or {}
        return {
            **mr,
            "number": mr.get("iid"),
            "html_url": mr.get("web_url") or "",
            "title": mr.get("title") or "",
            "state": "open" if gitlab_state == "opened" else "closed",
            "merged": gitlab_state == "merged",
            "head": {
                "ref": mr.get("source_branch") or "",
                "sha": mr.get("sha") or "",
                "repo": {"full_name": repo.owner},
            },
            "base": {"ref": mr.get("target_branch") or ""},
            "user": {"login": author.get("username") or ""},
            "author_association": "NONE",
        }

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
        """GitLab natively filters by source_branch/target_branch — no
        client-side filtering needed (unlike Gitea)."""
        _ = include_api_version
        params: dict[str, Any] = {"state": _STATE_PARAM_MAP.get(state, state)}
        if head is not None:
            params["source_branch"] = head
        if base is not None:
            params["target_branch"] = base
        if per_page is not None:
            params["per_page"] = per_page
        resp = await self._send(
            "get",
            self._url(repo, "merge_requests"),
            headers=self._headers(token),
            params=params,
            timeout=timeout,
        )
        if not resp.is_success:
            return resp
        mrs = resp.json()
        if not isinstance(mrs, list):
            return resp
        shaped = [self._shape_pull(mr, repo) for mr in mrs]
        return ShapedResponse(resp, json_payload=shaped)

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
        resp = await self._send(
            "get",
            self._url(repo, "merge_requests", str(pr_number)),
            headers=self._headers(token),
            timeout=timeout,
        )
        if not resp.is_success:
            return resp
        data = resp.json()
        if not isinstance(data, dict):
            return resp
        return ShapedResponse(resp, json_payload=self._shape_pull(data, repo))

    async def get_pr_diff(self, repo: RepoRef, token: str, pr_number: int) -> Any:
        """GitLab has no raw-unified-diff media type — ``/diffs`` returns one
        JSON object per changed file; reassemble a unified diff from up to
        3 pages of 100."""
        url = self._url(repo, "merge_requests", str(pr_number), "diffs")
        headers = self._headers(token)
        diffs: list[dict[str, Any]] = []
        resp = await self._send(
            "get", url, headers=headers, params={"page": 1, "per_page": _DIFF_PAGE_SIZE}
        )
        if not resp.is_success:
            return resp
        page = resp.json()
        if isinstance(page, list):
            diffs.extend(page)
        for page_number in range(2, _DIFF_MAX_PAGES + 1):
            if not isinstance(page, list) or len(page) < _DIFF_PAGE_SIZE:
                break
            resp = await self._send(
                "get",
                url,
                headers=headers,
                params={"page": page_number, "per_page": _DIFF_PAGE_SIZE},
            )
            if not resp.is_success:
                return resp
            page = resp.json()
            if isinstance(page, list):
                diffs.extend(page)
        text = "".join(
            f"diff --git a/{item.get('old_path', '')} b/{item.get('new_path', '')}\n"
            f"{item.get('diff', '')}"
            for item in diffs
        )
        return ShapedResponse(resp, text=text)

    async def create_pr(
        self, repo: RepoRef, token: str, *, head: str, base: str, title: str, body: str
    ) -> Any:
        """GitLab signals the duplicate-MR case as 409 where GitHub uses
        422; reshape that one case so GitService's idempotency branch
        (`422 and "already exists" in text`) keeps working."""
        resp = await self._send(
            "post",
            self._url(repo, "merge_requests"),
            headers=self._headers(token),
            json_body={
                "source_branch": head,
                "target_branch": base,
                "title": title,
                "description": body,
            },
        )
        if resp.status_code == httpx.codes.CONFLICT and "already exists" in resp.text:
            return ShapedResponse(resp, status_code=httpx.codes.UNPROCESSABLE_ENTITY)
        if not resp.is_success:
            return resp
        data = resp.json()
        if not isinstance(data, dict):
            return resp
        return ShapedResponse(resp, json_payload=self._shape_pull(data, repo))

    async def update_pr(
        self, repo: RepoRef, token: str, pr_number: int, *, payload: dict[str, Any]
    ) -> httpx.Response:
        """Translate GitHub's PATCH vocabulary onto GitLab's PUT one:
        ``body``→``description``, ``state=closed``→``state_event=close``."""
        translated: dict[str, Any] = {}
        if "title" in payload:
            translated["title"] = payload["title"]
        if "body" in payload:
            translated["description"] = payload["body"]
        if payload.get("state") == "closed":
            translated["state_event"] = "close"
        return await self._send(
            "put",
            self._url(repo, "merge_requests", str(pr_number)),
            headers=self._headers(token),
            json_body=translated,
        )

    async def merge_pr(
        self, repo: RepoRef, token: str, pr_number: int, *, merge_method: str
    ) -> httpx.Response:
        return await self._send(
            "put",
            self._url(repo, "merge_requests", str(pr_number), "merge"),
            headers=self._headers(token),
            json_body={"squash": merge_method == "squash"},
        )

    async def request_reviewers(
        self, repo: RepoRef, token: str, pr_number: int, reviewers: list[str]
    ) -> Any:
        """GitLab reviewer assignment needs numeric user ids
        (``reviewer_ids``) — RoboCo only ever stores usernames/slugs, and
        resolving those is out of scope for Phase 3 (spec's open items).
        Skip with a synthetic success rather than failing the PR-open flow
        over a best-effort reviewer mirror."""
        _ = (repo, token, pr_number, reviewers)
        request = httpx.Request("put", f"{self._scheme}://{self._host}/synthetic")
        real = httpx.Response(
            httpx.codes.OK,
            request=request,
            json={"skipped": "gitlab reviewer mirroring needs numeric ids"},
        )
        return ShapedResponse(real)

    async def post_review(
        self, repo: RepoRef, token: str, pr_number: int, *, body: str, event: str
    ) -> httpx.Response:
        """GitLab has no request-changes verb: APPROVE hits the dedicated
        approve endpoint, anything else (REQUEST_CHANGES/COMMENT) becomes a
        plain note carrying the verdict in its body."""
        if event == "APPROVE":
            return await self._send(
                "post",
                self._url(repo, "merge_requests", str(pr_number), "approve"),
                headers=self._headers(token),
            )
        return await self._send(
            "post",
            self._url(repo, "merge_requests", str(pr_number), "notes"),
            headers=self._headers(token),
            json_body={"body": body},
        )

    async def merge_branch(
        self, repo: RepoRef, token: str, *, base: str, head: str, commit_message: str
    ) -> Any:
        """No server-side merges API on GitLab — a shaped 501 lands
        ``_env_merge_status`` on its existing ``missing_ref`` branch (the
        shared local-git fallback lives in GitService)."""
        _ = (repo, token, base, head, commit_message)
        request = httpx.Request("post", f"{self._scheme}://{self._host}/unsupported")
        real = httpx.Response(
            httpx.codes.NOT_IMPLEMENTED,
            request=request,
            json={"message": "GitLab has no server-side branch-merge API"},
        )
        return ShapedResponse(real)

    # -- CI --------------------------------------------------------------------

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
        """GitLab pipelines are the CI signal (no per-workflow-run listing
        like GitHub Actions); pipelines sort newest-first by default, so the
        first SETTLED entry becomes the one ``workflow_runs`` entry."""
        _ = (workflow, head_sha)
        resp = await self._send(
            "get",
            self._url(repo, "pipelines"),
            headers=self._headers(token),
            params={"ref": branch, "per_page": per_page},
        )
        if not resp.is_success:
            return resp
        pipelines = resp.json()
        if not isinstance(pipelines, list):
            return resp
        runs: list[dict[str, Any]] = []
        for pipeline in pipelines:
            conclusion = _CONCLUSION_MAP.get(str(pipeline.get("status") or "").lower())
            if conclusion is None:
                continue
            runs.append(
                {
                    "head_sha": pipeline.get("sha") or "",
                    "run_attempt": 1,
                    "conclusion": conclusion,
                    "name": "pipeline",
                    "html_url": pipeline.get("web_url") or "",
                    "updated_at": pipeline.get("updated_at") or "",
                }
            )
            break
        return ShapedResponse(resp, json_payload={"workflow_runs": runs})

    async def list_check_runs(
        self, repo: RepoRef, token: str, head_sha: str, *, per_page: int
    ) -> Any:
        """Commit statuses reshaped into GitHub's ``check_runs`` envelope."""
        resp = await self._send(
            "get",
            self._url(repo, "repository", "commits", head_sha, "statuses"),
            headers=self._headers(token),
            params={"per_page": per_page},
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
        return {
            "id": status.get("id") or 0,
            "name": status.get("name") or "status",
            "status": "in_progress" if state in _IN_PROGRESS_STATUSES else "completed",
            "conclusion": _CONCLUSION_MAP.get(state),
        }

    async def list_workflows(self, repo: RepoRef, token: str, *, per_page: int) -> Any:
        """Any pipeline at all — even zero check-runs — means CI IS
        configured, so a pipelines-configured repo classifies
        ``pending_not_scheduled`` rather than ``no_ci_configured``."""
        _ = per_page
        resp = await self._send(
            "get",
            self._url(repo, "pipelines"),
            headers=self._headers(token),
            params={"per_page": 1},
        )
        if not resp.is_success:
            return resp
        pipelines = resp.json()
        total = 1 if isinstance(pipelines, list) and pipelines else 0
        return ShapedResponse(resp, json_payload={"total_count": total})

    # -- repo / labels / branches / releases -----------------------------------

    async def get_repo(
        self,
        repo: RepoRef,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
    ) -> Any:
        """GitLab names merge-method settings differently — reshape onto
        GitHub's ``allow_*`` keys the merge-method fallback reads."""
        resp = await self._send(
            "get",
            self._url(repo),
            headers=self._headers(token),
            client=client,
            timeout=timeout,
        )
        if not resp.is_success:
            return resp
        data = resp.json()
        if not isinstance(data, dict):
            return resp
        squash_option = data.get("squash_option")
        merge_method = data.get("merge_method")
        shaped = dict(data)
        shaped["full_name"] = data.get("path_with_namespace") or ""
        shaped["html_url"] = data.get("web_url") or ""
        shaped["clone_url"] = data.get("http_url_to_repo") or ""
        shaped["allow_squash_merge"] = (
            squash_option != "never" if squash_option else True
        )
        shaped["allow_merge_commit"] = merge_method in (None, "merge")
        shaped["allow_rebase_merge"] = merge_method in ("rebase_merge", "ff")
        return ShapedResponse(resp, json_payload=shaped)

    async def ensure_label(
        self, repo: RepoRef, token: str, name: str, color: str
    ) -> httpx.Response:
        # GitLab wants the leading '#' on label colors; GitHub omits it.
        hex_color = color if color.startswith("#") else f"#{color}"
        return await self._send(
            "post",
            self._url(repo, "labels"),
            headers=self._headers(token),
            json_body={"name": name, "color": hex_color},
        )

    async def add_labels(
        self, repo: RepoRef, token: str, pr_number: int, labels: list[str]
    ) -> httpx.Response:
        return await self._send(
            "put",
            self._url(repo, "merge_requests", str(pr_number)),
            headers=self._headers(token),
            json_body={"add_labels": ",".join(labels)},
        )

    async def delete_branch_ref(
        self, repo: RepoRef, token: str, branch: str, *, timeout: float | None = None
    ) -> httpx.Response:
        return await self._send(
            "delete",
            self._url(repo, "repository", "branches", quote(branch, safe="")),
            headers=self._headers(token),
            timeout=timeout,
        )

    async def create_issue_comment(
        self, repo: RepoRef, token: str, issue_number: int, body: str
    ) -> httpx.Response:
        # RoboCo only ever comments on PRs — GitLab's PR comments live under
        # the merge-request "notes" endpoint, not a separate issues API call.
        return await self._send(
            "post",
            self._url(repo, "merge_requests", str(issue_number), "notes"),
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
        resp = await self._send(
            "post",
            self._url(repo, "releases"),
            headers=self._headers(token),
            json_body={
                "tag_name": tag_name,
                "name": name,
                "description": body,
                "ref": target_commitish,
            },
            timeout=timeout,
        )
        if not resp.is_success:
            return resp
        data = resp.json()
        if not isinstance(data, dict):
            return resp
        links = data.get("_links") or {}
        shaped = dict(data)
        shaped["html_url"] = links.get("self") or ""
        return ShapedResponse(resp, json_payload=shaped)

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
        """GitLab has no ``/orgs/{org}/repos`` — resolve ``org`` (a group's
        full path, subgroups included) to its numeric namespace id and POST
        ``/projects`` under it. A 404 group lookup means ``org`` is a
        personal namespace instead: the project lands under the token's own
        namespace by omitting ``namespace_id`` entirely.

        Reshapes the 201 body onto the GitHub fields callers read
        (``full_name``/``clone_url``/``html_url``), mirroring ``get_repo``.
        GitLab signals a duplicate project path as 400 "has already been
        taken" where GitHub uses 422 "already exists" — reshaped to 422 so
        the status code lines up; the text is left untouched (the
        provisioning service's already-exists match covers both phrases).
        """
        headers = self._headers(token)
        base = f"{self._scheme}://{self._host}/api/v4"
        namespace_id = await self._resolve_namespace_id(
            org, headers=headers, client=client, timeout=timeout
        )
        if isinstance(namespace_id, httpx.Response | ShapedResponse):
            return namespace_id  # group lookup itself errored (not a 404)

        payload: dict[str, Any] = {
            "name": name,
            "path": _project_path(name),
            "description": description,
            "visibility": "private" if private else "internal",
            "initialize_with_readme": auto_init,
        }
        if namespace_id is not None:
            payload["namespace_id"] = namespace_id
        resp = await self._send(
            "post",
            f"{base}/projects",
            headers=headers,
            json_body=payload,
            client=client,
            timeout=timeout,
        )
        return self._shape_create_project(resp)

    async def _resolve_namespace_id(
        self,
        org: str,
        *,
        headers: dict[str, str],
        client: httpx.AsyncClient | None,
        timeout: float | None,
    ) -> int | None | httpx.Response | ShapedResponse:
        """The group's numeric id, ``None`` for a personal (404) namespace,
        or the raw error response for anything else."""
        group_resp = await self._send(
            "get",
            f"{self._scheme}://{self._host}/api/v4/groups/{quote(org, safe='')}",
            headers=headers,
            client=client,
            timeout=timeout,
        )
        if group_resp.is_success:
            group_data = group_resp.json()
            return group_data.get("id") if isinstance(group_data, dict) else None
        if group_resp.status_code == httpx.codes.NOT_FOUND:
            return None
        return group_resp

    @staticmethod
    def _shape_create_project(resp: httpx.Response) -> Any:
        """Reshape a project-create response onto the GitHub fields callers
        read, and GitLab's duplicate-path 400 onto GitHub's 422."""
        if (
            resp.status_code == httpx.codes.BAD_REQUEST
            and "has already been taken" in resp.text
        ):
            return ShapedResponse(resp, status_code=httpx.codes.UNPROCESSABLE_ENTITY)
        if not resp.is_success:
            return resp
        data = resp.json()
        if not isinstance(data, dict):
            return resp
        shaped = dict(data)
        shaped["full_name"] = data.get("path_with_namespace") or ""
        shaped["html_url"] = data.get("web_url") or ""
        shaped["clone_url"] = data.get("http_url_to_repo") or ""
        return ShapedResponse(resp, json_payload=shaped)

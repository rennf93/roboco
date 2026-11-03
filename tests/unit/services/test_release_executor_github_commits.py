"""On GitHub the release's commits are written by GitHub's API, so GitHub
signs them: ``_GitReleaseOps.commit_and_push`` re-creates the env-chain merges
(merges API) and the release commit (``createCommitOnBranch``) on a staging
branch, checks each holds the tree the clone built, then fast-forwards prod.

Driven end to end on real git: the clone comes from a bare origin through the
relay, as in production, and a fake GitHub serves the ref, merge and commit
endpoints over that same bare repo, so each test reads what landed on prod
straight from it.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import httpx
import pytest
from roboco.config import settings
from roboco.services import release_executor as re
from roboco.services.forge import registry
from roboco.services.git_relay import GitRelay
from roboco.services.release_executor import _GitReleaseOps, _ReleaseContext

if TYPE_CHECKING:
    from pathlib import Path

_STAGING = "release-manager/staging"
_REPO_PATH = "/repos/acme/app/"
_VERSION = "0.13.0"
_GITHUB_IDENTITY = {
    "GIT_AUTHOR_NAME": "roboco-app[bot]",
    "GIT_AUTHOR_EMAIL": "bot@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "GitHub",
    "GIT_COMMITTER_EMAIL": "noreply@github.com",
}


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    return result.stdout.strip()


def _git_rc(repo: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    return result.returncode, result.stdout.strip()


class _FakeGitHub:
    """GitHub's ref, merge and commit-on-branch endpoints over a bare repo.

    Each endpoint keeps GitHub's contract the executor relies on: refs are
    created only when absent, a non-forced update must fast-forward (422
    otherwise), a merge answers 201 with the merge commit and its tree, and
    ``createCommitOnBranch`` refuses a stale ``expectedHeadOid``. The
    ``fail_*`` knobs inject the failures GitHub can answer with.
    """

    def __init__(self, origin: Path) -> None:
        self.origin = origin
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict[str, Any]] = []
        self.fail_commit_with_errors = False
        self.fail_merge_with_status: int | None = None
        self.report_wrong_merge_tree = False
        self.drop_connection_on_commit = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        self.calls.append((request.method, path))
        self.bodies.append(body)
        if path == "/graphql":
            return self._commit_on_branch(request, body["variables"]["input"])
        route = path.removeprefix(_REPO_PATH)
        branch = route.removeprefix("git/refs/heads/")
        if request.method == "POST" and route == "git/refs":
            return self._create_ref(body["ref"], body["sha"])
        if request.method == "PATCH" and route.startswith("git/refs/heads/"):
            return self._update_ref(branch, body["sha"], force=body["force"])
        if request.method == "DELETE" and route.startswith("git/refs/heads/"):
            _git(self.origin, "update-ref", "-d", f"refs/heads/{branch}")
            return httpx.Response(204)
        if request.method == "POST" and route == "merges":
            return self._merge(body["base"], body["head"], body["commit_message"])
        return httpx.Response(404, json={"message": "Not Found"})

    def head_of(self, branch: str) -> str | None:
        _, sha = _git_rc(
            self.origin, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"
        )
        return sha or None

    def _create_ref(self, ref: str, sha: str) -> httpx.Response:
        branch = ref.removeprefix("refs/heads/")
        if self.head_of(branch) is not None:
            return httpx.Response(422, json={"message": "Reference already exists"})
        _git(self.origin, "update-ref", ref, sha)
        return httpx.Response(201, json={"ref": ref, "object": {"sha": sha}})

    def _update_ref(self, branch: str, sha: str, *, force: bool) -> httpx.Response:
        current = self.head_of(branch)
        if current is None:
            return httpx.Response(422, json={"message": "Reference does not exist"})
        ancestor_rc, _ = _git_rc(
            self.origin, "merge-base", "--is-ancestor", current, sha
        )
        if not force and ancestor_rc != 0:
            return httpx.Response(422, json={"message": "Update is not a fast forward"})
        _git(self.origin, "update-ref", f"refs/heads/{branch}", sha)
        return httpx.Response(200, json={"object": {"sha": sha}})

    def _merge(self, base_branch: str, head: str, message: str) -> httpx.Response:
        if self.fail_merge_with_status is not None:
            return httpx.Response(
                self.fail_merge_with_status, json={"message": "Server Error"}
            )
        base = self.head_of(base_branch)
        assert base is not None
        tree = _git(self.origin, "merge-tree", "--write-tree", base, head)
        sha = _git(
            self.origin,
            "commit-tree",
            tree,
            "-p",
            base,
            "-p",
            head,
            "-m",
            message,
            env=_GITHUB_IDENTITY,
        )
        _git(self.origin, "update-ref", f"refs/heads/{base_branch}", sha, base)
        reported_tree = "f" * 40 if self.report_wrong_merge_tree else tree
        return httpx.Response(
            201, json={"sha": sha, "commit": {"tree": {"sha": reported_tree}}}
        )

    def _commit_on_branch(
        self, request: httpx.Request, commit_input: dict[str, Any]
    ) -> httpx.Response:
        if self.drop_connection_on_commit:
            raise httpx.ConnectError("connection reset", request=request)
        branch = commit_input["branch"]["branchName"]
        head = self.head_of(branch)
        expected = commit_input["expectedHeadOid"]
        if head is None or head != expected or self.fail_commit_with_errors:
            return httpx.Response(
                200,
                json={"errors": [{"message": "Expected branch to point to X"}]},
            )
        index = {"GIT_INDEX_FILE": str(self.origin / "fake-github-index")}
        _git(self.origin, "read-tree", head, env=index)
        for addition in commit_input["fileChanges"]["additions"]:
            blob = (
                subprocess.run(
                    ["git", "-C", str(self.origin), "hash-object", "-w", "--stdin"],
                    input=base64.b64decode(addition["contents"]),
                    capture_output=True,
                    check=True,
                )
                .stdout.decode()
                .strip()
            )
            _git(
                self.origin,
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{blob},{addition['path']}",
                env=index,
            )
        for deletion in commit_input["fileChanges"]["deletions"]:
            _git(
                self.origin,
                "update-index",
                "--force-remove",
                deletion["path"],
                env=index,
            )
        tree = _git(self.origin, "write-tree", env=index)
        sha = _git(
            self.origin,
            "commit-tree",
            tree,
            "-p",
            head,
            "-m",
            commit_input["message"]["headline"],
            env=_GITHUB_IDENTITY,
        )
        _git(self.origin, "update-ref", f"refs/heads/{branch}", sha, head)
        commit = {"oid": sha, "tree": {"oid": tree}}
        return httpx.Response(
            200, json={"data": {"createCommitOnBranch": {"commit": commit}}}
        )


def _seed(repo: Path) -> None:
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit_file(repo: Path, name: str, text: str, message: str) -> str:
    (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _origin_with_ladder(tmp_path: Path, *, hotfix_on_master: bool) -> Path:
    """A bare origin with ``master`` (prod) and ``slave`` (head rung) where
    slave carries a feature commit; with ``hotfix_on_master`` master carries
    one slave lacks, so promotion is a real merge instead of a fast-forward."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True
    )
    subprocess.run(["git", "init", "-q", "-b", "master", str(seed)], check=True)
    _seed(seed)
    (seed / "pyproject.toml").write_text('[project]\nversion = "0.12.0"\n')
    (seed / "CHANGELOG.md").write_text("# Changelog\n")
    _commit_file(seed, "README.md", "base\n", "init")
    _git(seed, "checkout", "-q", "-b", "slave")
    _commit_file(seed, "feature.py", "FEATURE = True\n", "feat: a thing")
    _git(seed, "checkout", "-q", "master")
    if hotfix_on_master:
        _commit_file(seed, "hotfix.py", "FIX = True\n", "fix: a hotfix")
    _git(seed, "push", "-q", str(origin), "master", "slave")
    return origin


async def _bumped_release_ops(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    origin: Path,
    *,
    git_url: str = "https://github.com/acme/app",
) -> _GitReleaseOps:
    """The production ops on a fresh relay clone, promoted and bumped: the
    state ``commit_and_push`` runs in."""
    monkeypatch.setattr(settings, "private_data_root", str(tmp_path / "private"))
    relay = GitRelay("acme-app", f"file://{origin}", None)
    root = await re._prepare_release_clone(relay, "master")
    ctx = _ReleaseContext(
        slug="acme-app",
        prod_branch="master",
        root=root,
        git_url=git_url,
        relay=relay,
        ci_workflow=None,
        env_chain=["slave"],
    )
    ops = _GitReleaseOps(session=MagicMock(), ctx=ctx)

    async def _token(_self: _GitReleaseOps) -> str:
        return "SECRET"

    monkeypatch.setattr(_GitReleaseOps, "_forge_token", _token)
    await ops.promote_env_chain()
    await ops.apply_version_bumps(["pyproject.toml", "CHANGELOG.md"], _VERSION)
    await ops.write_changelog_entry(f"## [{_VERSION}] - 2026-10-29\n\n- a thing\n")
    return ops


def _serve(monkeypatch: pytest.MonkeyPatch, github: _FakeGitHub) -> None:
    real_client = httpx.AsyncClient

    def _factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(github.handler))

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


@pytest.fixture(autouse=True)
def _clean_host_map() -> None:
    registry._HOST_PROVIDERS.clear()


async def test_merge_and_release_commit_are_written_by_github_then_prod_fast_forwards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    origin = _origin_with_ladder(tmp_path, hotfix_on_master=True)
    old_master = _git(origin, "rev-parse", "master")
    slave = _git(origin, "rev-parse", "slave")
    ops = await _bumped_release_ops(monkeypatch, tmp_path, origin)
    github = _FakeGitHub(origin)
    _serve(monkeypatch, github)

    landed = await ops.commit_and_push(_VERSION)

    clone = ops._root
    master = _git(origin, "rev-parse", "master")
    assert landed == master
    # GitHub wrote its own commits (never the clone's), with the clone's trees.
    assert landed != _git(clone, "rev-parse", "HEAD")
    assert _git(origin, "rev-parse", "master^{tree}") == _git(
        clone, "rev-parse", "HEAD^{tree}"
    )
    assert (
        _git(origin, "log", "-1", "--format=%s", "master") == "chore(release): 0.13.0"
    )
    assert _git(origin, "log", "-1", "--format=%cn", "master") == "GitHub"
    merge_parents = _git(origin, "log", "-1", "--format=%P", "master^").split()
    assert merge_parents == [old_master, slave]
    assert _git(origin, "rev-parse", "master^^{tree}") == _git(
        clone, "rev-parse", "HEAD^^{tree}"
    )
    assert github.calls == [
        ("POST", "/repos/acme/app/git/refs"),
        ("POST", "/repos/acme/app/merges"),
        ("POST", "/graphql"),
        ("PATCH", "/repos/acme/app/git/refs/heads/master"),
        ("DELETE", f"/repos/acme/app/git/refs/heads/{_STAGING}"),
    ]
    staging_ref, merge, commit, fast_forward, _ = github.bodies
    assert staging_ref == {"ref": f"refs/heads/{_STAGING}", "sha": old_master}
    assert merge["base"] == _STAGING
    assert merge["head"] == slave
    commit_input = commit["variables"]["input"]
    assert commit_input["expectedHeadOid"] == _git(origin, "rev-parse", "master^")
    assert {a["path"] for a in commit_input["fileChanges"]["additions"]} == {
        "pyproject.toml",
        "CHANGELOG.md",
    }
    assert fast_forward == {"sha": landed, "force": False}
    assert github.head_of(_STAGING) is None


async def test_fast_forward_promotion_moves_staging_without_a_merge_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Git fast-forwarded prod onto the rung, so GitHub does too: no merge
    commit, the release commit sits directly on the rung tip."""
    origin = _origin_with_ladder(tmp_path, hotfix_on_master=False)
    slave = _git(origin, "rev-parse", "slave")
    ops = await _bumped_release_ops(monkeypatch, tmp_path, origin)
    github = _FakeGitHub(origin)
    _serve(monkeypatch, github)

    landed = await ops.commit_and_push(_VERSION)

    assert _git(origin, "rev-parse", "master") == landed
    assert _git(origin, "rev-parse", "master^") == slave
    assert ("POST", "/repos/acme/app/merges") not in github.calls
    assert github.bodies[1] == {"sha": slave, "force": False}
    assert github.calls[1] == ("PATCH", f"/repos/acme/app/git/refs/heads/{_STAGING}")


async def test_a_prod_that_moved_refuses_the_fast_forward_and_nothing_lands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The API twin of a rejected non-fast-forward push: the commit raises
    (``commit_failed`` upstream), prod keeps the commit someone else landed."""
    origin = _origin_with_ladder(tmp_path, hotfix_on_master=True)
    ops = await _bumped_release_ops(monkeypatch, tmp_path, origin)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _seed(other)
    concurrent = _commit_file(other, "late.py", "LATE = 1\n", "fix: landed meanwhile")
    _git(other, "push", "-q", "origin", "master")
    github = _FakeGitHub(origin)
    _serve(monkeypatch, github)

    with pytest.raises(RuntimeError, match=r"release push failed: fast-forward master"):
        await ops.commit_and_push(_VERSION)

    assert _git(origin, "rev-parse", "master") == concurrent
    assert github.head_of(_STAGING) is None


@pytest.mark.parametrize(
    ("knob", "message"),
    [
        ("fail_commit_with_errors", r"commit the release: .*Expected branch"),
        ("fail_merge_with_status", r"merge [0-9a-f]{12}: HTTP 500"),
        ("report_wrong_merge_tree", r"GitHub's slave merge has tree ffffffffffff"),
        ("drop_connection_on_commit", r"release push failed: connection reset"),
    ],
)
async def test_a_failed_api_call_leaves_prod_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, knob: str, message: str
) -> None:
    origin = _origin_with_ladder(tmp_path, hotfix_on_master=True)
    old_master = _git(origin, "rev-parse", "master")
    ops = await _bumped_release_ops(monkeypatch, tmp_path, origin)
    github = _FakeGitHub(origin)
    setattr(github, knob, 500 if knob == "fail_merge_with_status" else True)
    _serve(monkeypatch, github)

    with pytest.raises(RuntimeError, match=message):
        await ops.commit_and_push(_VERSION)

    assert _git(origin, "rev-parse", "master") == old_master
    assert ("PATCH", "/repos/acme/app/git/refs/heads/master") not in github.calls
    assert github.head_of(_STAGING) is None


async def test_a_staging_branch_left_by_an_interrupted_run_is_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    origin = _origin_with_ladder(tmp_path, hotfix_on_master=True)
    _git(
        origin,
        "update-ref",
        f"refs/heads/{_STAGING}",
        _git(origin, "rev-parse", "slave"),
    )
    old_master = _git(origin, "rev-parse", "master")
    ops = await _bumped_release_ops(monkeypatch, tmp_path, origin)
    github = _FakeGitHub(origin)
    _serve(monkeypatch, github)

    landed = await ops.commit_and_push(_VERSION)

    assert _git(origin, "rev-parse", "master") == landed
    assert github.calls[1] == ("PATCH", f"/repos/acme/app/git/refs/heads/{_STAGING}")
    assert github.bodies[1] == {"sha": old_master, "force": True}


async def test_a_forge_without_server_side_commits_pushes_the_clone_with_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Gitea's adapter answers 501: the clone's own commits land by push,
    exactly as before, and no request reaches any API."""
    git_url = "https://gitea.acme.test/acme/app.git"
    registry.register_project_forge(git_url, "gitea")
    origin = _origin_with_ladder(tmp_path, hotfix_on_master=True)
    ops = await _bumped_release_ops(monkeypatch, tmp_path, origin, git_url=git_url)
    github = _FakeGitHub(origin)
    _serve(monkeypatch, github)

    landed = await ops.commit_and_push(_VERSION)

    assert landed == _git(ops._root, "rev-parse", "HEAD")
    assert _git(origin, "rev-parse", "master") == landed
    assert github.calls == []


async def test_no_token_fails_closed_before_any_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    origin = _origin_with_ladder(tmp_path, hotfix_on_master=True)
    old_master = _git(origin, "rev-parse", "master")
    ops = await _bumped_release_ops(monkeypatch, tmp_path, origin)

    async def _no_token(_self: _GitReleaseOps) -> None:
        return None

    monkeypatch.setattr(_GitReleaseOps, "_forge_token", _no_token)
    github = _FakeGitHub(origin)
    _serve(monkeypatch, github)

    with pytest.raises(RuntimeError, match="no git token"):
        await ops.commit_and_push(_VERSION)

    assert _git(origin, "rev-parse", "master") == old_master
    assert github.calls == []

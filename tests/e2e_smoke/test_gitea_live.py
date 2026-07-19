"""Live-Gitea contract test for GiteaProvider (forge Phase 2).

Runs the real wire contract against a live Gitea instance — the spec's
"contract suite recorded against a dockerized gitea/gitea" — and is fully
self-seeding: it creates its own uniquely-named repo, pushes real commits,
and exercises the provider end to end (PR open → duplicate reshape →
list/filter → diff → comment review → commit-status CI reshape → squash
merge → branch delete → release), plus the git-CLI Basic-auth extraheader
claim the provider docstring makes.

Skipped unless both env vars are set:

    ROBOCO_GITEA_E2E_URL    e.g. http://localhost:3310
    ROBOCO_GITEA_E2E_TOKEN  an admin PAT (scopes: all)

Local run: `docker run -d -p 3310:3000 -e GITEA__security__INSTALL_LOCK=true
gitea/gitea:1.22`, create an admin + token (`gitea admin user create` /
`generate-access-token`), export the two vars, run this file.
"""

from __future__ import annotations

import base64
import os
import subprocess
import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from roboco.services.forge.base import RepoRef
from roboco.services.forge.gitea import GiteaProvider

if TYPE_CHECKING:
    from pathlib import Path

_URL = os.environ.get("ROBOCO_GITEA_E2E_URL", "")
_TOKEN = os.environ.get("ROBOCO_GITEA_E2E_TOKEN", "")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not (_URL and _TOKEN),
        reason="ROBOCO_GITEA_E2E_URL / ROBOCO_GITEA_E2E_TOKEN not set",
    ),
]


def _split_url() -> tuple[str, str]:
    parts = urlsplit(_URL)
    host = parts.netloc or parts.path
    return (parts.scheme or "http"), host


def _api(method: str, path: str, **kwargs: object) -> httpx.Response:
    scheme, host = _split_url()
    return httpx.request(
        method,
        f"{scheme}://{host}/api/v1{path}",
        headers={"Authorization": f"token {_TOKEN}"},
        timeout=15.0,
        **kwargs,  # type: ignore[arg-type]
    )


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _seed_repo(tmp_path: Path, repo_name: str) -> str:
    """Create the repo via API, push a feature commit; return its sha."""
    resp = _api(
        "post",
        "/user/repos",
        json={"name": repo_name, "auto_init": True, "default_branch": "main"},
    )
    assert resp.status_code == httpx.codes.CREATED, resp.text
    login = _api("get", "/user").json()["username"]
    scheme, host = _split_url()
    clone_url = f"{scheme}://{login}:{_TOKEN}@{host}/{login}/{repo_name}.git"
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", clone_url, str(clone)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(clone, "config", "user.email", "e2e@example.com")
    _git(clone, "config", "user.name", "E2E")
    _git(clone, "config", "commit.gpgsign", "false")
    _git(clone, "checkout", "-b", "feat/e2e-change")
    (clone / "widget.txt").write_text("widget v2\n")
    _git(clone, "add", "widget.txt")
    _git(clone, "commit", "-m", "add widget")
    _git(clone, "push", "-u", "origin", "feat/e2e-change")
    return _git(clone, "rev-parse", "HEAD")


async def _verify_pr_flow(provider: GiteaProvider, ref: RepoRef, head_sha: str) -> int:
    """create → duplicate reshape → list/filter → get → diff → review →
    labels; returns the PR number."""
    created = await provider.create_pr(
        ref,
        _TOKEN,
        head="feat/e2e-change",
        base="main",
        title="E2E change",
        body="live contract test",
    )
    assert created.is_success, created.text
    pr = created.json()
    pr_number: int = pr["number"]
    assert pr["html_url"]

    duplicate = await provider.create_pr(
        ref,
        _TOKEN,
        head="feat/e2e-change",
        base="main",
        title="E2E change",
        body="dup",
    )
    assert duplicate.status_code == httpx.codes.UNPROCESSABLE_ENTITY
    assert "already exists" in duplicate.text.lower()

    listed = await provider.list_pulls(ref, _TOKEN, head="feat/e2e-change", base="main")
    pulls = listed.json()
    assert [p["number"] for p in pulls] == [pr_number]
    assert pulls[0]["author_association"] == "NONE"

    fetched = (await provider.get_pr(ref, _TOKEN, pr_number)).json()
    assert fetched["head"]["sha"] == head_sha
    assert fetched["base"]["ref"] == "main"
    assert not fetched.get("merged")

    diff = await provider.get_pr_diff(ref, _TOKEN, pr_number)
    assert "widget.txt" in diff.text

    # COMMENT — self-review approve/request-changes is refused by Gitea.
    review = await provider.post_review(
        ref, _TOKEN, pr_number, body="looks fine", event="COMMENT"
    )
    assert review.is_success, review.text

    label = await provider.ensure_label(ref, _TOKEN, "cell/backend", "8250df")
    assert label.is_success or label.status_code in (409, 422), label.text
    attach = await provider.add_labels(ref, _TOKEN, pr_number, ["cell/backend"])
    assert attach.is_success, attach.text
    return pr_number


async def _verify_ci_reshapes(
    provider: GiteaProvider, ref: RepoRef, head_sha: str
) -> None:
    """A real commit status classifies through both GitHub-shaped views."""
    status = _api(
        "post",
        f"/repos/{ref.owner}/{ref.repo}/statuses/{head_sha}",
        json={"state": "success", "context": "ci/e2e", "description": "ok"},
    )
    assert status.status_code == httpx.codes.CREATED, status.text
    check_runs = (
        await provider.list_check_runs(ref, _TOKEN, head_sha, per_page=50)
    ).json()["check_runs"]
    assert check_runs and check_runs[0]["conclusion"] == "success"
    assert check_runs[0]["status"] == "completed"
    runs = (
        await provider.list_ci_runs(
            ref,
            _TOKEN,
            workflow=None,
            branch="feat/e2e-change",
            head_sha=None,
            per_page=5,
        )
    ).json()["workflow_runs"]
    assert runs and runs[0]["conclusion"] == "success"
    assert runs[0]["head_sha"] == head_sha


async def _verify_merge_publish_cli(
    provider: GiteaProvider, ref: RepoRef, pr_number: int, scheme: str
) -> None:
    """Squash merge → merged flag → branch delete → release → CLI auth."""
    merged = await provider.merge_pr(ref, _TOKEN, pr_number, merge_method="squash")
    assert merged.is_success, merged.text
    for _ in range(10):
        if (await provider.get_pr(ref, _TOKEN, pr_number)).json().get("merged"):
            break
        time.sleep(0.5)
    assert (await provider.get_pr(ref, _TOKEN, pr_number)).json()["merged"] is True

    deleted = await provider.delete_branch_ref(ref, _TOKEN, "feat/e2e-change")
    assert deleted.status_code in (204, 200), deleted.text

    release = await provider.create_release(
        ref,
        _TOKEN,
        tag_name="v0.0.1-e2e",
        name="v0.0.1-e2e",
        body="live",
        target_commitish="main",
    )
    assert release.status_code == httpx.codes.CREATED, release.text
    assert release.json().get("html_url")

    # The provider docstring's git-CLI claim: Basic auth with the
    # x-access-token username + PAT password works against Gitea.
    basic = base64.b64encode(f"x-access-token:{_TOKEN}".encode()).decode()
    ls = subprocess.run(
        [
            "git",
            "-c",
            f"http.extraheader=Authorization: Basic {basic}",
            "ls-remote",
            f"{scheme}://{ref.host}/{ref.owner}/{ref.repo}.git",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ls.returncode == 0, ls.stderr
    assert "refs/heads/main" in ls.stdout


async def test_gitea_live_contract(tmp_path: Path) -> None:
    scheme, host = _split_url()
    repo_name = f"e2e-{uuid4().hex[:8]}"
    head_sha = _seed_repo(tmp_path, repo_name)
    login = _api("get", "/user").json()["username"]
    ref = RepoRef(login, repo_name, host=host)
    provider = GiteaProvider(host, scheme=scheme)

    repo_resp = await provider.get_repo(ref, _TOKEN)
    assert repo_resp.is_success
    repo_json = repo_resp.json()
    assert repo_json["full_name"] == f"{login}/{repo_name}"
    assert "allow_merge_commit" in repo_json
    assert "allow_squash_merge" in repo_json

    pr_number = await _verify_pr_flow(provider, ref, head_sha)
    await _verify_ci_reshapes(provider, ref, head_sha)
    await _verify_merge_publish_cli(provider, ref, pr_number, scheme)

    _api("delete", f"/repos/{login}/{repo_name}")

"""Live-GitLab contract test for GitLabProvider (forge Phase 3).

Mirror of ``test_gitea_live.py``: fully self-seeding against a real GitLab
instance (gitlab.com works) — creates a uniquely-named private project under
the token's namespace, pushes real commits, and drives the provider end to
end: MR open → duplicate reshape → native list/filter → GitHub-shape
adaptation → diff reassembly → note review → commit-status CI reshape →
squash merge → branch delete → release → the oauth2 Basic-auth git-CLI
claim. The project is deleted afterwards (best-effort).

Skipped unless both env vars are set:

    ROBOCO_GITLAB_E2E_URL    e.g. https://gitlab.com
    ROBOCO_GITLAB_E2E_TOKEN  a PAT with `api` scope

Run tip: the token is a secret — export it in the shell, never write it
into a file or a compose entry.
"""

from __future__ import annotations

import base64
import os
import subprocess
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
import pytest
from roboco.services.forge.base import RepoRef
from roboco.services.forge.gitlab import GitLabProvider

if TYPE_CHECKING:
    from pathlib import Path

_URL = os.environ.get("ROBOCO_GITLAB_E2E_URL", "")
_TOKEN = os.environ.get("ROBOCO_GITLAB_E2E_TOKEN", "")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not (_URL and _TOKEN),
        reason="ROBOCO_GITLAB_E2E_URL / ROBOCO_GITLAB_E2E_TOKEN not set",
    ),
]

_MERGE_SETTLE_ATTEMPTS = 20


def _split_url() -> tuple[str, str]:
    parts = urlsplit(_URL)
    host = parts.netloc or parts.path
    return (parts.scheme or "https"), host


def _api(method: str, path: str, **kwargs: object) -> httpx.Response:
    scheme, host = _split_url()
    return httpx.request(
        method,
        f"{scheme}://{host}/api/v4{path}",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        timeout=30.0,
        **kwargs,  # type: ignore[arg-type]
    )


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _seed_project(tmp_path: Path, name: str) -> tuple[str, str]:
    """Create the project, push a feature commit; return (path, head_sha)."""
    resp = _api(
        "post",
        "/projects",
        json={
            "name": name,
            "visibility": "private",
            "initialize_with_readme": True,
            "default_branch": "main",
        },
    )
    assert resp.status_code == httpx.codes.CREATED, resp.text
    path_with_namespace = resp.json()["path_with_namespace"]
    scheme, host = _split_url()
    clone_url = f"{scheme}://oauth2:{_TOKEN}@{host}/{path_with_namespace}.git"
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
    return path_with_namespace, _git(clone, "rev-parse", "HEAD")


async def _verify_mr_flow(provider: GitLabProvider, ref: RepoRef, head_sha: str) -> int:
    """create → duplicate reshape → native filter → adapted object → diff →
    note review; returns the MR iid (as GitHub-shape ``number``)."""
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
    assert pr["head"]["ref"] == "feat/e2e-change"
    assert pr["base"]["ref"] == "main"

    duplicate = await provider.create_pr(
        ref,
        _TOKEN,
        head="feat/e2e-change",
        base="main",
        title="E2E change",
        body="dup",
    )
    assert duplicate.status_code == httpx.codes.UNPROCESSABLE_ENTITY, (
        duplicate.status_code
    )
    assert "already exists" in duplicate.text.lower()

    listed = await provider.list_pulls(ref, _TOKEN, head="feat/e2e-change", base="main")
    pulls = listed.json()
    assert [p["number"] for p in pulls] == [pr_number]
    assert pulls[0]["author_association"] == "NONE"

    fetched = (await provider.get_pr(ref, _TOKEN, pr_number)).json()
    assert fetched["head"]["sha"] == head_sha
    assert not fetched.get("merged")

    diff = await provider.get_pr_diff(ref, _TOKEN, pr_number)
    assert "widget.txt" in diff.text

    review = await provider.post_review(
        ref, _TOKEN, pr_number, body="looks fine", event="COMMENT"
    )
    assert review.is_success, review.text
    return pr_number


async def _verify_ci_reshapes(
    provider: GitLabProvider, ref: RepoRef, head_sha: str
) -> None:
    """A real commit status classifies through the check_runs reshape; the
    pipelines-based views stay well-shaped for a pipeline-less project."""
    status = _api(
        "post",
        f"/projects/{quote(ref.owner, safe='')}/statuses/{head_sha}",
        json={"state": "success", "context": "ci/e2e"},
    )
    assert status.status_code in (200, 201), status.text
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
    ).json()
    assert "workflow_runs" in runs
    workflows = (await provider.list_workflows(ref, _TOKEN, per_page=1)).json()
    assert "total_count" in workflows


async def _verify_merge_publish_cli(
    provider: GitLabProvider, ref: RepoRef, pr_number: int, scheme: str
) -> None:
    """Squash merge (retrying while GitLab computes mergeability) → merged
    flag → branch delete → release → oauth2 Basic-auth CLI claim."""
    merged_resp = None
    for _ in range(_MERGE_SETTLE_ATTEMPTS):
        merged_resp = await provider.merge_pr(
            ref, _TOKEN, pr_number, merge_method="squash"
        )
        if merged_resp.is_success:
            break
        time.sleep(1.0)
    assert merged_resp is not None and merged_resp.is_success, merged_resp.text
    for _ in range(_MERGE_SETTLE_ATTEMPTS):
        if (await provider.get_pr(ref, _TOKEN, pr_number)).json().get("merged"):
            break
        time.sleep(0.5)
    assert (await provider.get_pr(ref, _TOKEN, pr_number)).json()["merged"] is True

    deleted = await provider.delete_branch_ref(ref, _TOKEN, "feat/e2e-change")
    assert deleted.status_code in (204, 200, 404), deleted.text

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

    basic = base64.b64encode(f"oauth2:{_TOKEN}".encode()).decode()
    ls = subprocess.run(
        [
            "git",
            "-c",
            f"http.extraheader=Authorization: Basic {basic}",
            "ls-remote",
            f"{scheme}://{ref.host}/{ref.owner}.git",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ls.returncode == 0, ls.stderr
    assert "refs/heads/main" in ls.stdout


async def test_gitlab_live_contract(tmp_path: Path) -> None:
    scheme, host = _split_url()
    project_name = f"roboco-e2e-{uuid4().hex[:8]}"
    path_with_namespace, head_sha = _seed_project(tmp_path, project_name)
    ref = RepoRef(path_with_namespace, "", host=host)
    provider = GitLabProvider(host, scheme=scheme)

    parsed = provider.parse_repo_ref(f"{scheme}://{host}/{path_with_namespace}.git")
    assert parsed == ref

    repo_resp = await provider.get_repo(ref, _TOKEN)
    assert repo_resp.is_success, repo_resp.text
    repo_json: dict[str, Any] = repo_resp.json()
    assert repo_json["full_name"] == path_with_namespace
    assert "allow_squash_merge" in repo_json
    assert "allow_merge_commit" in repo_json

    pr_number = await _verify_mr_flow(provider, ref, head_sha)
    await _verify_ci_reshapes(provider, ref, head_sha)
    await _verify_merge_publish_cli(provider, ref, pr_number, scheme)

    _api("delete", f"/projects/{quote(path_with_namespace, safe='')}")

"""Server-side commits: GitHub creates and moves branch refs over REST and
writes a commit from file contents over GraphQL (so GitHub signs it); Gitea
and GitLab answer shaped 501s, which the release executor reads as "push
with git"."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from roboco.services.forge import GiteaProvider, GitHubProvider, RepoRef, registry
from roboco.services.forge.gitlab import GitLabProvider
from roboco.services.forge.router import ForgeRouter

if TYPE_CHECKING:
    from collections.abc import Callable

_REF = RepoRef("acme", "app")
_OK = 200
_NOT_IMPLEMENTED = 501


class _Recorder:
    def __init__(self, responder: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._responder = responder

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)


def _patch_client(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    real_client = httpx.AsyncClient

    def _factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(recorder.handler))

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


@pytest.fixture(autouse=True)
def _clean_host_map() -> None:
    registry._HOST_PROVIDERS.clear()


async def test_github_creates_a_branch_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(201, json={}))
    _patch_client(monkeypatch, recorder)

    await GitHubProvider().create_branch_ref(
        _REF, "SECRET", "release-manager/staging", sha="abc123"
    )

    (request,) = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.github.com/repos/acme/app/git/refs"
    assert request.headers["Authorization"] == "Bearer SECRET"
    assert json.loads(request.content) == {
        "ref": "refs/heads/release-manager/staging",
        "sha": "abc123",
    }


@pytest.mark.parametrize("force", [False, True])
async def test_github_moves_a_branch_ref(
    monkeypatch: pytest.MonkeyPatch, force: bool
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(_OK, json={}))
    _patch_client(monkeypatch, recorder)

    await GitHubProvider().update_branch_ref(
        _REF, "SECRET", "release-manager/staging", sha="def456", force=force
    )

    (request,) = recorder.requests
    assert request.method == "PATCH"
    assert str(request.url) == (
        "https://api.github.com/repos/acme/app/git/refs/heads/release-manager/staging"
    )
    assert json.loads(request.content) == {"sha": "def456", "force": force}


async def test_github_commits_file_contents_through_graphql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contents travel as standard base64 of the exact bytes (binary-safe),
    the head expectation and the target branch ride the input."""
    recorder = _Recorder(lambda _r: httpx.Response(_OK, json={"data": {}}))
    _patch_client(monkeypatch, recorder)
    binary = bytes(range(256))

    await GitHubProvider().create_commit_on_branch(
        _REF,
        "SECRET",
        "release-manager/staging",
        expected_head_oid="0" * 40,
        headline="chore(release): 1.2.3",
        additions={"pyproject.toml": b'version = "1.2.3"\n', "blob.bin": binary},
        deletions=["old.txt"],
    )

    (request,) = recorder.requests
    assert str(request.url) == "https://api.github.com/graphql"
    assert request.headers["Authorization"] == "Bearer SECRET"
    body = json.loads(request.content)
    assert "createCommitOnBranch" in body["query"]
    commit_input = body["variables"]["input"]
    assert commit_input["branch"] == {
        "repositoryNameWithOwner": "acme/app",
        "branchName": "release-manager/staging",
    }
    assert commit_input["expectedHeadOid"] == "0" * 40
    assert commit_input["message"] == {"headline": "chore(release): 1.2.3"}
    additions = {
        a["path"]: base64.b64decode(a["contents"], validate=True)
        for a in commit_input["fileChanges"]["additions"]
    }
    assert additions == {"pyproject.toml": b'version = "1.2.3"\n', "blob.bin": binary}
    assert commit_input["fileChanges"]["deletions"] == [{"path": "old.txt"}]


@pytest.mark.parametrize(
    "provider", [GiteaProvider("gitea.acme.test"), GitLabProvider("gitlab.acme.test")]
)
async def test_other_forges_answer_server_side_commits_with_a_shaped_501(
    provider: Any,
) -> None:
    responses = [
        await provider.create_branch_ref(_REF, "SECRET", "b", sha="abc"),
        await provider.update_branch_ref(_REF, "SECRET", "b", sha="abc", force=False),
        await provider.create_commit_on_branch(
            _REF,
            "SECRET",
            "b",
            expected_head_oid="abc",
            headline="h",
            additions={"f": b"x"},
            deletions=[],
        ),
    ]
    for resp in responses:
        assert resp.status_code == _NOT_IMPLEMENTED
        assert "push with git" in resp.json()["message"]


async def test_router_routes_server_side_commits_by_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """github.com refs reach GitHub's API; a registered Gitea host gets its
    adapter's 501 without any request leaving the process."""
    recorder = _Recorder(lambda _r: httpx.Response(201, json={}))
    _patch_client(monkeypatch, recorder)
    registry.register_project_forge("https://gitea.acme.test/acme/app.git", "gitea")
    router = ForgeRouter()

    github = await router.create_branch_ref(_REF, "SECRET", "b", sha="abc")
    gitea = await router.create_branch_ref(
        RepoRef("acme", "app", host="gitea.acme.test"), "SECRET", "b", sha="abc"
    )

    assert github.status_code == httpx.codes.CREATED
    assert gitea.status_code == _NOT_IMPLEMENTED
    assert len(recorder.requests) == 1

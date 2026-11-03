"""``_GitReleaseOps.publish_release`` publishes through the project's forge:
GitHub, Gitea and GitLab each get their own releases endpoint and payload, and
a remote no forge resolves fails closed as ``publish_failed`` instead of
escaping ``execute`` as a 500."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import httpx
import pytest
from roboco.services.forge import registry
from roboco.services.git_relay import GitRelay
from roboco.services.release_executor import (
    ReleaseExecutor,
    _GitReleaseOps,
    _ReleaseContext,
)
from roboco.services.release_readiness import ReleaseReadinessReport

if TYPE_CHECKING:
    from collections.abc import Callable

_CREATED = 201


class _Recorder:
    def __init__(self, responder: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._responder = responder

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)


def _serve(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    real_client = httpx.AsyncClient

    def _factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(recorder.handler))

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


def _ops(monkeypatch: pytest.MonkeyPatch, git_url: str) -> _GitReleaseOps:
    ctx = _ReleaseContext(
        slug="acme-app",
        prod_branch="main",
        root=Path("/nonexistent/release-clone"),
        git_url=git_url,
        relay=GitRelay("acme-app", git_url, None),
        ci_workflow=None,
        env_chain=[],
    )

    async def _token(_self: _GitReleaseOps) -> str:
        return "SECRET"

    monkeypatch.setattr(_GitReleaseOps, "_forge_token", _token)
    return _GitReleaseOps(session=MagicMock(), ctx=ctx)


@pytest.fixture(autouse=True)
def _clean_host_map() -> None:
    registry._HOST_PROVIDERS.clear()
    registry._HOST_SCHEMES.clear()


@dataclass(frozen=True)
class _ForgeCase:
    """What one forge's releases API must receive, and what it answers."""

    git_provider: str
    git_url: str
    endpoint: str
    authorization: str
    payload: dict[str, str]
    reply: dict[str, Any]
    release_url: str


_GITHUB = _ForgeCase(
    git_provider="github",
    git_url="https://github.com/acme/app.git",
    endpoint="https://api.github.com/repos/acme/app/releases",
    authorization="Bearer SECRET",
    payload={
        "tag_name": "v1.2.0",
        "name": "v1.2.0",
        "body": "notes",
        "target_commitish": "main",
    },
    reply={"html_url": "https://github.com/acme/app/releases/tag/v1.2.0"},
    release_url="https://github.com/acme/app/releases/tag/v1.2.0",
)
_GITEA = _ForgeCase(
    git_provider="gitea",
    git_url="https://gitea.acme.test/acme/app.git",
    endpoint="https://gitea.acme.test/api/v1/repos/acme/app/releases",
    authorization="token SECRET",
    payload={
        "tag_name": "v1.2.0",
        "name": "v1.2.0",
        "body": "notes",
        "target_commitish": "main",
    },
    reply={"html_url": "https://gitea.acme.test/acme/app/releases/tag/v1.2.0"},
    release_url="https://gitea.acme.test/acme/app/releases/tag/v1.2.0",
)
# GitLab answers no html_url; its adapter reshapes _links.self into one.
_GITLAB = _ForgeCase(
    git_provider="gitlab",
    git_url="https://gitlab.acme.test/acme/app.git",
    endpoint="https://gitlab.acme.test/api/v4/projects/acme%2Fapp/releases",
    authorization="Bearer SECRET",
    payload={
        "tag_name": "v1.2.0",
        "name": "v1.2.0",
        "description": "notes",
        "ref": "main",
    },
    reply={
        "tag_name": "v1.2.0",
        "_links": {"self": "https://gitlab.acme.test/acme/app/-/releases/v1.2.0"},
    },
    release_url="https://gitlab.acme.test/acme/app/-/releases/v1.2.0",
)


@pytest.mark.parametrize(
    "case", [_GITHUB, _GITEA, _GITLAB], ids=lambda c: c.git_provider
)
async def test_each_forge_publishes_through_its_own_releases_api(
    monkeypatch: pytest.MonkeyPatch, case: _ForgeCase
) -> None:
    registry.register_project_forge(case.git_url, case.git_provider)
    recorder = _Recorder(lambda _r: httpx.Response(_CREATED, json=case.reply))
    _serve(monkeypatch, recorder)

    url = await _ops(monkeypatch, case.git_url).publish_release("1.2.0", "notes")

    (request,) = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == case.endpoint
    assert request.headers["Authorization"] == case.authorization
    assert json.loads(request.content) == case.payload
    assert url == case.release_url


@pytest.mark.parametrize("case", [_GITEA, _GITLAB], ids=lambda c: c.git_provider)
async def test_a_refused_publish_on_another_forge_fails_closed(
    monkeypatch: pytest.MonkeyPatch, case: _ForgeCase
) -> None:
    registry.register_project_forge(case.git_url, case.git_provider)
    recorder = _Recorder(
        lambda _r: httpx.Response(409, json={"message": "release already exists"})
    )
    _serve(monkeypatch, recorder)

    with pytest.raises(RuntimeError, match="release publish failed: HTTP 409"):
        await _ops(monkeypatch, case.git_url).publish_release("1.2.0", "notes")

    (request,) = recorder.requests
    assert str(request.url) == case.endpoint


class _HalfLandedOps(_GitReleaseOps):
    """A publish_failed retry: the release commit is on prod and CI is
    green, so ``execute`` goes straight to the real ``publish_release``."""

    async def is_already_published(self, version: str) -> bool:
        _ = version
        return False

    async def release_commit_sha(self, version: str) -> str | None:
        _ = version
        return "a" * 40

    async def wait_for_ci(self, commit_sha: str) -> bool:
        _ = commit_sha
        return True


async def test_a_remote_no_forge_resolves_ends_publish_failed_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unregistered self-hosted host used to raise ``GitError`` straight
    out of ``execute``; it is now the structured ``publish_failed`` outcome
    and no request is sent."""
    git_url = "https://git.internal.acme.test/acme/app.git"
    recorder = _Recorder(lambda _r: httpx.Response(_CREATED, json={}))
    _serve(monkeypatch, recorder)
    _ops(monkeypatch, git_url)  # installs the token stub
    ctx = _ReleaseContext(
        slug="acme-app",
        prod_branch="main",
        root=Path("/nonexistent/release-clone"),
        git_url=git_url,
        relay=GitRelay("acme-app", git_url, None),
        ci_workflow=None,
        env_chain=[],
    )
    report = ReleaseReadinessReport(
        proposed_version="1.2.0",
        bump_kind="minor",
        change_summary=["feat: a thing"],
        drafted_changelog="## [1.2.0]\n",
        version_bump_plan=["pyproject.toml"],
        gaps=[],
        migration_notes=[],
        gate_state="green",
    )

    result = await ReleaseExecutor(_HalfLandedOps(MagicMock(), ctx)).execute(report)

    assert result.status == "publish_failed"
    assert result.release_url is None
    assert "registered project" in result.detail
    assert recorder.requests == []

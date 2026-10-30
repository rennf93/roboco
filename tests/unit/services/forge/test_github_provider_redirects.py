"""GitHubProvider follows GitHub's rename / transfer redirects.

GitHub answers an API call to a renamed or transferred repo with 301 (reads)
or 307 (writes) to its new location. Live 2026-10-28 seven rennf93/* repos had
moved to the Guard-Core org and every list_open_prs poll for them came back
301. Uses httpx.MockTransport through the provider's own ``_send`` (the
``client=``-less path), injected by patching ``httpx.AsyncClient`` the way the
Gitea provider suite does, with every client kwarg passed through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from roboco.services.forge.base import RepoRef
from roboco.services.forge.github import GitHubProvider

if TYPE_CHECKING:
    from collections.abc import Callable

_MOVED = RepoRef("rennf93", "fastapi-guard")
_NEW_BASE = "https://api.github.com/repositories/838357935"
_HTTP_OK = 200
_HTTP_CREATED = 201


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
        return real_client(transport=httpx.MockTransport(recorder.handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_a_read_follows_the_301_to_the_moved_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/repos/rennf93/fastapi-guard"):
            return httpx.Response(
                301, headers={"Location": f"{_NEW_BASE}/pulls?state=open"}
            )
        return httpx.Response(_HTTP_OK, json=[{"number": 7}])

    recorder = _Recorder(respond)
    _patch_client(monkeypatch, recorder)

    resp = await GitHubProvider(base_url="https://api.github.com").list_pulls(
        _MOVED, "tok"
    )

    assert resp.status_code == _HTTP_OK
    assert resp.json() == [{"number": 7}]
    assert [r.url.path for r in recorder.requests] == [
        "/repos/rennf93/fastapi-guard/pulls",
        "/repositories/838357935/pulls",
    ]
    assert recorder.requests[1].headers["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_a_write_follows_the_307_with_its_method_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/repos/rennf93/fastapi-guard"):
            return httpx.Response(307, headers={"Location": f"{_NEW_BASE}/pulls"})
        return httpx.Response(_HTTP_CREATED, json={"number": 8})

    recorder = _Recorder(respond)
    _patch_client(monkeypatch, recorder)

    resp = await GitHubProvider(base_url="https://api.github.com")._send(
        "post",
        "https://api.github.com/repos/rennf93/fastapi-guard/pulls",
        headers={"Authorization": "Bearer tok"},
        json_body={"title": "t", "head": "h", "base": "b"},
    )

    assert resp.status_code == _HTTP_CREATED
    moved = recorder.requests[1]
    assert moved.method == "POST"
    assert moved.url.path == "/repositories/838357935/pulls"
    assert moved.content == recorder.requests[0].content

"""GitService.post_pr_review — posts ONE review to a PR (httpx fully mocked).

Verifies the request shape (the first ``/pulls/{n}/reviews`` call in the
codebase), Bearer auth, and that GitHub/token failures raise ``GitError`` so the
calling side-effect can surface them.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.git import GitError, GitService

_PR = 42


def _service() -> GitService:
    session = MagicMock()
    session.execute = AsyncMock()
    return GitService(session)


def _bind(svc: GitService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _resp(
    *, status_code: int, json_payload: dict[str, Any] | None = None, text: str = ""
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300  # noqa: PLR2004
    resp.json.return_value = json_payload or {}
    resp.text = text
    return resp


def _client(post_resp: MagicMock) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=post_resp)
    return client


def _patch_project() -> Any:
    fake = MagicMock()
    fake.get_by_slug = AsyncMock(
        return_value=MagicMock(git_url="https://github.com/acme/repo.git")
    )
    return patch("roboco.services.git.get_project_service", return_value=fake)


@pytest.mark.asyncio
async def test_post_pr_review_posts_request_changes() -> None:
    svc = _service()
    _bind(svc, "_token_for_project", AsyncMock(return_value="tok"))
    client = _client(
        _resp(status_code=200, json_payload={"id": 1, "state": "CHANGES_REQUESTED"})
    )
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await svc.post_pr_review("roboco", _PR, "Please fix X.")
    client.post.assert_awaited_once()
    call = client.post.await_args
    assert f"/pulls/{_PR}/reviews" in call.args[0]
    assert call.kwargs["json"] == {"body": "Please fix X.", "event": "REQUEST_CHANGES"}
    assert call.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert out["state"] == "CHANGES_REQUESTED"


@pytest.mark.asyncio
async def test_post_pr_review_raises_on_missing_token() -> None:
    svc = _service()
    _bind(svc, "_token_for_project", AsyncMock(return_value=None))
    with _patch_project(), pytest.raises(GitError):
        await svc.post_pr_review("roboco", _PR, "body")


@pytest.mark.asyncio
async def test_post_pr_review_raises_on_github_error() -> None:
    svc = _service()
    _bind(svc, "_token_for_project", AsyncMock(return_value="tok"))
    client = _client(_resp(status_code=422, text="Unprocessable"))
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
        pytest.raises(GitError),
    ):
        await svc.post_pr_review("roboco", _PR, "body")

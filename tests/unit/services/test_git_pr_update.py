"""Unit tests for GitService.update_pr_for_task.

Covers PATCH (title/body) + POST reviewers (requested_reviewers) round-trips
against GitHub's REST API. httpx is fully mocked — no real network.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from roboco.exceptions import GitError
from roboco.services.base import NotFoundError
from roboco.services.git import GitService

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

_PR_NUMBER = 42
_HTTP_NOT_FOUND = 404
_HTTP_UNPROCESSABLE = 422


def _make_session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _service() -> GitService:
    return GitService(_make_session())


def _patch_project_service(project: object | None) -> AbstractContextManager[object]:
    fake_service = MagicMock()
    fake_service.get = AsyncMock(return_value=project)
    fake_service.get_by_slug = AsyncMock(return_value=project)
    return patch("roboco.services.git.get_project_service", return_value=fake_service)


def _bind(svc: GitService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _task_with_pr(pr_number: int | None = _PR_NUMBER, pr_url: str = "") -> MagicMock:
    """Build a TaskTable-shaped MagicMock with a pr_number set."""
    project_id = uuid4()
    return MagicMock(
        id=uuid4(),
        project_id=project_id,
        pr_number=pr_number,
        pr_url=pr_url or f"https://github.com/acme/repo/pull/{pr_number}",
        assigned_to=uuid4(),
        created_by=uuid4(),
    )


def _make_http_response(
    *, status_code: int, json_payload: dict[str, Any] | None = None, text: str = ""
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300  # noqa: PLR2004
    resp.json.return_value = json_payload or {}
    resp.text = text
    return resp


def _make_async_client(
    *,
    patch_resp: MagicMock | None = None,
    post_resp: MagicMock | None = None,
) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.patch = AsyncMock(return_value=patch_resp)
    client.post = AsyncMock(return_value=post_resp)
    return client


async def _stub_task_get(svc: GitService, task: object | None) -> None:
    task_service = MagicMock()
    task_service.get = AsyncMock(return_value=task)
    _bind(svc, "_task_service_for_pr_update", task_service)


def _wire_service(svc: GitService, task: MagicMock) -> MagicMock:
    """Apply common bindings: workspace, remote parse, token resolution."""
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))

    # update_pr_for_task fetches the task via get_task_service; we patch it
    # at the module level.
    fake_task_service = MagicMock()
    fake_task_service.get = AsyncMock(return_value=task)
    return fake_task_service


@pytest.mark.asyncio
async def test_update_pr_title_only_calls_patch_with_title() -> None:
    """title-only call PATCHes /pulls/{n} with {title: ...} and no reviewers POST."""
    svc = _service()
    task = _task_with_pr()
    fake_task_service = _wire_service(svc, task)
    fake_project = MagicMock(slug="roboco")

    patch_resp = _make_http_response(
        status_code=200,
        json_payload={
            "number": _PR_NUMBER,
            "html_url": task.pr_url,
            "title": "new title",
        },
    )
    fake_client = _make_async_client(patch_resp=patch_resp)

    with (
        patch("roboco.services.git.get_task_service", return_value=fake_task_service),
        _patch_project_service(fake_project),
        patch("roboco.services.git.httpx.AsyncClient", return_value=fake_client),
    ):
        out = await svc.update_pr_for_task(
            UUID(str(task.id)), title="new title", body=None, reviewers=None
        )

    fake_client.patch.assert_awaited_once()
    fake_client.post.assert_not_awaited()
    call = fake_client.patch.await_args
    assert f"/pulls/{_PR_NUMBER}" in call.args[0]
    assert call.kwargs["json"] == {"title": "new title"}
    assert out["pr_number"] == _PR_NUMBER
    assert out["updated_fields"] == ["title"]


@pytest.mark.asyncio
async def test_update_pr_title_and_body_calls_patch_with_both() -> None:
    """Both title and body in the same PATCH payload."""
    svc = _service()
    task = _task_with_pr()
    fake_task_service = _wire_service(svc, task)
    fake_project = MagicMock(slug="roboco")

    patch_resp = _make_http_response(
        status_code=200,
        json_payload={"number": _PR_NUMBER, "html_url": task.pr_url},
    )
    fake_client = _make_async_client(patch_resp=patch_resp)

    with (
        patch("roboco.services.git.get_task_service", return_value=fake_task_service),
        _patch_project_service(fake_project),
        patch("roboco.services.git.httpx.AsyncClient", return_value=fake_client),
    ):
        out = await svc.update_pr_for_task(
            UUID(str(task.id)), title="t", body="b", reviewers=None
        )

    fake_client.patch.assert_awaited_once()
    call = fake_client.patch.await_args
    assert call.kwargs["json"] == {"title": "t", "body": "b"}
    assert set(out["updated_fields"]) == {"title", "body"}


@pytest.mark.asyncio
async def test_update_pr_reviewers_only_calls_post_reviewers() -> None:
    """reviewers-only call POSTs to /pulls/{n}/requested_reviewers, skips PATCH."""
    svc = _service()
    task = _task_with_pr()
    fake_task_service = _wire_service(svc, task)
    fake_project = MagicMock(slug="roboco")

    post_resp = _make_http_response(
        status_code=201,
        json_payload={"number": _PR_NUMBER, "html_url": task.pr_url},
    )
    fake_client = _make_async_client(post_resp=post_resp)

    with (
        patch("roboco.services.git.get_task_service", return_value=fake_task_service),
        _patch_project_service(fake_project),
        patch("roboco.services.git.httpx.AsyncClient", return_value=fake_client),
    ):
        out = await svc.update_pr_for_task(
            UUID(str(task.id)),
            title=None,
            body=None,
            reviewers=["be-dev-2", "be-qa"],
        )

    fake_client.patch.assert_not_awaited()
    fake_client.post.assert_awaited_once()
    call = fake_client.post.await_args
    assert f"/pulls/{_PR_NUMBER}/requested_reviewers" in call.args[0]
    assert call.kwargs["json"] == {"reviewers": ["be-dev-2", "be-qa"]}
    assert out["updated_fields"] == ["reviewers"]


@pytest.mark.asyncio
async def test_update_pr_all_three_fields_forwarded() -> None:
    """title + body + reviewers → one PATCH + one POST, both fields reported."""
    svc = _service()
    task = _task_with_pr()
    fake_task_service = _wire_service(svc, task)
    fake_project = MagicMock(slug="roboco")

    patch_resp = _make_http_response(
        status_code=200, json_payload={"number": _PR_NUMBER, "html_url": task.pr_url}
    )
    post_resp = _make_http_response(
        status_code=201, json_payload={"number": _PR_NUMBER, "html_url": task.pr_url}
    )
    fake_client = _make_async_client(patch_resp=patch_resp, post_resp=post_resp)

    with (
        patch("roboco.services.git.get_task_service", return_value=fake_task_service),
        _patch_project_service(fake_project),
        patch("roboco.services.git.httpx.AsyncClient", return_value=fake_client),
    ):
        out = await svc.update_pr_for_task(
            UUID(str(task.id)), title="t", body="b", reviewers=["be-dev-2"]
        )

    fake_client.patch.assert_awaited_once()
    fake_client.post.assert_awaited_once()
    assert set(out["updated_fields"]) == {"title", "body", "reviewers"}


@pytest.mark.asyncio
async def test_update_pr_404_raises_pr_not_found() -> None:
    """HTTP 404 from PATCH → GitError mentioning PR not found."""
    svc = _service()
    task = _task_with_pr()
    fake_task_service = _wire_service(svc, task)
    fake_project = MagicMock(slug="roboco")

    patch_resp = _make_http_response(status_code=_HTTP_NOT_FOUND, text="Not Found")
    fake_client = _make_async_client(patch_resp=patch_resp)

    with (
        patch("roboco.services.git.get_task_service", return_value=fake_task_service),
        _patch_project_service(fake_project),
        patch("roboco.services.git.httpx.AsyncClient", return_value=fake_client),
        pytest.raises(GitError, match="PR not found"),
    ):
        await svc.update_pr_for_task(
            UUID(str(task.id)), title="t", body=None, reviewers=None
        )


@pytest.mark.asyncio
async def test_update_pr_422_raises_with_validation_message() -> None:
    """HTTP 422 from PATCH → GitError surfacing the validation text."""
    svc = _service()
    task = _task_with_pr()
    fake_task_service = _wire_service(svc, task)
    fake_project = MagicMock(slug="roboco")

    patch_resp = _make_http_response(
        status_code=_HTTP_UNPROCESSABLE, text="Validation Failed: body too long"
    )
    fake_client = _make_async_client(patch_resp=patch_resp)

    with (
        patch("roboco.services.git.get_task_service", return_value=fake_task_service),
        _patch_project_service(fake_project),
        patch("roboco.services.git.httpx.AsyncClient", return_value=fake_client),
        pytest.raises(GitError, match="Validation Failed"),
    ):
        await svc.update_pr_for_task(
            UUID(str(task.id)), title=None, body="long body", reviewers=None
        )


@pytest.mark.asyncio
async def test_update_pr_task_missing_raises_not_found() -> None:
    """Unknown task_id → NotFoundError (caller maps to invalid_state envelope)."""
    svc = _service()
    fake_task_service = MagicMock()
    fake_task_service.get = AsyncMock(return_value=None)

    with (
        patch("roboco.services.git.get_task_service", return_value=fake_task_service),
        pytest.raises(NotFoundError),
    ):
        await svc.update_pr_for_task(uuid4(), title="t", body=None, reviewers=None)


@pytest.mark.asyncio
async def test_update_pr_no_pr_number_raises_git_error() -> None:
    """Task exists but has no pr_number → GitError ('no PR open')."""
    svc = _service()
    task = _task_with_pr(pr_number=None)
    fake_task_service = MagicMock()
    fake_task_service.get = AsyncMock(return_value=task)

    with (
        patch("roboco.services.git.get_task_service", return_value=fake_task_service),
        pytest.raises(GitError, match="no PR"),
    ):
        await svc.update_pr_for_task(
            UUID(str(task.id)), title="t", body=None, reviewers=None
        )

"""GitService.close_task_pr_best_effort (GAP A) — cancellation never closed
a task's own PR. Mirrors ``delete_task_branch``: resolves owner/repo
straight off the project's ``git_url``, no workspace/clone needed, so it's
safe to call from the cancel chokepoint for any task — assigned or not.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import roboco.services.git as git_module
from roboco.services.git import GitService

_PR_NUMBER = 42


def _bind(svc: GitService, name: str, value: object) -> None:
    setattr(svc, name, value)


def _service() -> GitService:
    svc = GitService.__new__(GitService)
    _bind(svc, "log", MagicMock())
    _bind(svc, "session", MagicMock())
    return svc


def _wire_project(
    monkeypatch: pytest.MonkeyPatch,
    *,
    git_url: str | None = "https://github.com/acme/repo.git",
) -> None:
    project = SimpleNamespace(git_url=git_url) if git_url else None
    project_svc = MagicMock(get_by_slug=AsyncMock(return_value=project))
    monkeypatch.setattr(git_module, "get_project_service", lambda _s: project_svc)


def _resp(*, status_code: int = 200, json_payload: dict[str, Any] | None = None) -> Any:
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300  # noqa: PLR2004
    resp.json.return_value = json_payload or {}
    return resp


@pytest.mark.asyncio
async def test_closes_an_open_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _service()
    _bind(svc, "_token_for_project", AsyncMock(return_value="tok"))
    _wire_project(monkeypatch)

    forge = MagicMock(
        get_pr=AsyncMock(return_value=_resp(json_payload={"state": "open"})),
        update_pr=AsyncMock(return_value=_resp()),
    )
    monkeypatch.setattr(GitService, "_forge", property(lambda _self: forge))

    out = await svc.close_task_pr_best_effort("roboco", _PR_NUMBER)

    assert out is True
    forge.update_pr.assert_awaited_once()
    call = forge.update_pr.await_args
    assert call.args[2] == _PR_NUMBER
    assert call.kwargs["payload"] == {"state": "closed"}


@pytest.mark.asyncio
async def test_already_closed_pr_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _service()
    _bind(svc, "_token_for_project", AsyncMock(return_value="tok"))
    _wire_project(monkeypatch)

    forge = MagicMock(
        get_pr=AsyncMock(return_value=_resp(json_payload={"state": "closed"})),
        update_pr=AsyncMock(),
    )
    monkeypatch.setattr(GitService, "_forge", property(lambda _self: forge))

    out = await svc.close_task_pr_best_effort("roboco", _PR_NUMBER)

    assert out is False
    forge.update_pr.assert_not_awaited()


@pytest.mark.asyncio
async def test_merged_pr_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    # A merged PR also reports state="closed" on GitHub — never re-close it.
    svc = _service()
    _bind(svc, "_token_for_project", AsyncMock(return_value="tok"))
    _wire_project(monkeypatch)

    forge = MagicMock(
        get_pr=AsyncMock(
            return_value=_resp(json_payload={"state": "closed", "merged": True})
        ),
        update_pr=AsyncMock(),
    )
    monkeypatch.setattr(GitService, "_forge", property(lambda _self: forge))

    out = await svc.close_task_pr_best_effort("roboco", _PR_NUMBER)

    assert out is False
    forge.update_pr.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_token_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _service()
    _bind(svc, "_token_for_project", AsyncMock(return_value=None))
    forge = MagicMock(get_pr=AsyncMock(), update_pr=AsyncMock())
    monkeypatch.setattr(GitService, "_forge", property(lambda _self: forge))

    out = await svc.close_task_pr_best_effort("roboco", _PR_NUMBER)

    assert out is False
    forge.get_pr.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_project_git_url_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _service()
    _bind(svc, "_token_for_project", AsyncMock(return_value="tok"))
    _wire_project(monkeypatch, git_url=None)
    forge = MagicMock(get_pr=AsyncMock(), update_pr=AsyncMock())
    monkeypatch.setattr(GitService, "_forge", property(lambda _self: forge))

    out = await svc.close_task_pr_best_effort("roboco", _PR_NUMBER)

    assert out is False
    forge.get_pr.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_transport_error_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _service()
    _bind(svc, "_token_for_project", AsyncMock(return_value="tok"))
    _wire_project(monkeypatch)

    forge = MagicMock(
        get_pr=AsyncMock(side_effect=httpx.HTTPError("boom")),
        update_pr=AsyncMock(),
    )
    monkeypatch.setattr(GitService, "_forge", property(lambda _self: forge))

    out = await svc.close_task_pr_best_effort("roboco", _PR_NUMBER)

    assert out is False
    forge.update_pr.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_pr_non_success_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _service()
    _bind(svc, "_token_for_project", AsyncMock(return_value="tok"))
    _wire_project(monkeypatch)

    forge = MagicMock(
        get_pr=AsyncMock(return_value=_resp(status_code=404)),
        update_pr=AsyncMock(),
    )
    monkeypatch.setattr(GitService, "_forge", property(lambda _self: forge))

    out = await svc.close_task_pr_best_effort("roboco", _PR_NUMBER)

    assert out is False
    forge.update_pr.assert_not_awaited()

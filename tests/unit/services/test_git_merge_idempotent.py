"""``_merge_with_retry`` treats an already-merged PR as idempotent success.

A merge PUT on an already-merged PR returns the same 405 as a genuine
"not mergeable" conflict. Treating it as a conflict made `cell_pm_complete`
try to rebase/escalate a PR that had already landed — the block<->unblock
respawn loop. The merge path now disambiguates: already-merged → success
(no-op), otherwise a real `MergeConflictError`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.exceptions import MergeConflictError
from roboco.services.git import GitService


def _git_service() -> GitService:
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()
    return svc


def _resp(status_code: int, *, is_success: bool) -> Any:
    return type(
        "R",
        (),
        {
            "status_code": status_code,
            "is_success": is_success,
            "text": "",
            "json": lambda _self=None: {},
        },
    )()


def _ctx() -> Any:
    return GitService._MergeContext(
        owner="acme",
        repo="repo",
        pr_number=42,
        git_token="tok",
        workspace=Path("/ws"),
        target="feature/main_pm/abc",
    )


@pytest.mark.asyncio
async def test_merge_idempotent_when_pr_already_merged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _git_service()
    monkeypatch.setattr(
        svc, "_call_merge_api", AsyncMock(return_value=_resp(405, is_success=False))
    )
    already = AsyncMock(return_value=True)
    monkeypatch.setattr(svc, "_pr_is_merged", already)

    # Must NOT raise — an already-merged PR is a no-op success.
    await svc._merge_with_retry(_ctx())

    already.assert_awaited_once()


@pytest.mark.asyncio
async def test_merge_raises_conflict_when_not_already_merged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _git_service()
    monkeypatch.setattr(
        svc, "_call_merge_api", AsyncMock(return_value=_resp(405, is_success=False))
    )
    monkeypatch.setattr(svc, "_pr_is_merged", AsyncMock(return_value=False))

    with pytest.raises(MergeConflictError):
        await svc._merge_with_retry(_ctx())


@pytest.mark.asyncio
async def test_pr_is_merged_true_when_github_reports_merged() -> None:
    svc = _git_service()
    resp = type(
        "R", (), {"is_success": True, "json": lambda _self=None: {"merged": True}}
    )()
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=resp)
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        assert await svc._pr_is_merged("acme", "repo", 42, "tok") is True


@pytest.mark.asyncio
async def test_pr_is_merged_false_on_non_success() -> None:
    svc = _git_service()
    resp = type("R", (), {"is_success": False, "json": lambda _self=None: {}})()
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=resp)
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        assert await svc._pr_is_merged("acme", "repo", 42, "tok") is False

"""GitService must not delete a branch that still has open dependent PRs.

Root cause of the run-zombifying "integration branch gone from origin" wedge:
`_delete_remote_branch_best_effort` deleted a merged PR's head branch
unconditionally. Merging a cell→root PR therefore deleted the cell branch out
from under in-flight leaf PRs still targeting it (and the CEO root→master merge
deleted the `feature/main_pm/{root}` integration branch). The fix guards the
deletion chokepoint: a branch that is still the BASE of any open PR is an active
integration target and is preserved. Fails safe — if the check can't run, the
branch is kept (cleanup is best-effort; stranding is not).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.git import GitService


def _service() -> GitService:
    session = MagicMock()
    session.execute = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    return GitService(session)


def _bind(svc: GitService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _fake_client() -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.delete = AsyncMock()
    client.get = AsyncMock()
    return client


# --- the deletion chokepoint guard ----------------------------------------


@pytest.mark.asyncio
async def test_delete_skips_branch_with_open_dependents() -> None:
    svc = _service()
    _bind(svc, "_branch_has_open_dependents", AsyncMock(return_value=True))
    client = _fake_client()
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        await svc._delete_remote_branch_best_effort(
            "acme", "repo", "feature/main_pm/abc123", "tok"
        )
    client.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_removes_leaf_branch_with_no_dependents() -> None:
    svc = _service()
    _bind(svc, "_branch_has_open_dependents", AsyncMock(return_value=False))
    client = _fake_client()
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        await svc._delete_remote_branch_best_effort(
            "acme", "repo", "feature/backend/abc--cell--leaf", "tok"
        )
    client.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_skips_default_branch_before_checking_dependents() -> None:
    svc = _service()
    dep = AsyncMock(return_value=False)
    _bind(svc, "_branch_has_open_dependents", dep)
    client = _fake_client()
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        await svc._delete_remote_branch_best_effort("acme", "repo", "master", "tok")
    client.delete.assert_not_awaited()
    dep.assert_not_awaited()


# --- the open-dependents probe --------------------------------------------


@pytest.mark.asyncio
async def test_has_open_dependents_true_when_open_pr_targets_base() -> None:
    svc = _service()
    resp = MagicMock(is_success=True)
    resp.json.return_value = [{"number": 5}]
    client = _fake_client()
    client.get = AsyncMock(return_value=resp)
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        out = await svc._branch_has_open_dependents(
            "acme", "repo", "feature/main_pm/abc123", "tok"
        )
    assert out is True


@pytest.mark.asyncio
async def test_has_open_dependents_false_when_none() -> None:
    svc = _service()
    resp = MagicMock(is_success=True)
    resp.json.return_value = []
    client = _fake_client()
    client.get = AsyncMock(return_value=resp)
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        out = await svc._branch_has_open_dependents(
            "acme", "repo", "feature/x--leaf", "tok"
        )
    assert out is False


@pytest.mark.asyncio
async def test_has_open_dependents_fails_safe_on_non_success() -> None:
    svc = _service()
    resp = MagicMock(is_success=False)
    client = _fake_client()
    client.get = AsyncMock(return_value=resp)
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        out = await svc._branch_has_open_dependents(
            "acme", "repo", "feature/main_pm/abc123", "tok"
        )
    assert out is True

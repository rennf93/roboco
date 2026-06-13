"""GitService falls back to a permitted merge method when the repo refuses the
requested one with 405 (PR #120).

A repo can disable a merge button (e.g. "Squash merges are not allowed on this
repository"), which 405s the merge and would permanently wedge the PM at task
completion with an open, mergeable PR. ``merge_pull_request`` retries once with
a method the repo actually permits, resolved by ``_first_allowed_merge_method``.
httpx is fully mocked — no real network.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.exceptions import GitError
from roboco.services.git import GitService

_PR = 42
_METHOD_NOT_ALLOWED = 405


def _service() -> GitService:
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    svc = GitService(session)
    object.__setattr__(svc, "log", MagicMock())
    return svc


def _bind(svc: GitService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _resp(
    status_code: int, json_payload: dict | None = None, text: str = ""
) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.is_success = 200 <= status_code < 300  # noqa: PLR2004
    r.json.return_value = json_payload or {}
    r.text = text
    return r


def _get_client(get_resp: MagicMock) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=get_resp)
    return client


# ---------------------------------------------------------------------------
# _first_allowed_merge_method — resolve a method the repo permits.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_allowed_prefers_squash() -> None:
    """All three permitted → squash is preferred."""
    svc = _service()
    client = _get_client(
        _resp(
            200,
            {
                "allow_squash_merge": True,
                "allow_merge_commit": True,
                "allow_rebase_merge": True,
            },
        )
    )
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        assert await svc._first_allowed_merge_method("acme", "repo", "tok") == "squash"


@pytest.mark.asyncio
async def test_first_allowed_skips_excluded_and_disabled() -> None:
    """exclude=squash and squash disabled → next permitted is merge."""
    svc = _service()
    client = _get_client(
        _resp(
            200,
            {
                "allow_squash_merge": False,
                "allow_merge_commit": True,
                "allow_rebase_merge": True,
            },
        )
    )
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        m = await svc._first_allowed_merge_method(
            "acme", "repo", "tok", exclude="squash"
        )
    assert m == "merge"


@pytest.mark.asyncio
async def test_first_allowed_returns_only_remaining_method() -> None:
    """Only rebase enabled (squash excluded, merge disabled) → rebase."""
    svc = _service()
    client = _get_client(
        _resp(
            200,
            {
                "allow_squash_merge": False,
                "allow_merge_commit": False,
                "allow_rebase_merge": True,
            },
        )
    )
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        m = await svc._first_allowed_merge_method(
            "acme", "repo", "tok", exclude="squash"
        )
    assert m == "rebase"


@pytest.mark.asyncio
async def test_first_allowed_none_on_api_failure() -> None:
    """Repo lookup fails → None (caller leaves the original refusal to surface)."""
    svc = _service()
    client = _get_client(_resp(404, text="Not Found"))
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        assert await svc._first_allowed_merge_method("acme", "repo", "tok") is None


# ---------------------------------------------------------------------------
# merge_pull_request — 405 fallback + retry.
# ---------------------------------------------------------------------------


def _wire_merge(svc: GitService) -> None:
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_delete_pr_branch_best_effort", AsyncMock())
    _bind(svc, "_project_default_branch", AsyncMock(return_value="main"))
    _bind(svc, "_sync_target_branch", AsyncMock(return_value="abc123"))


@pytest.mark.asyncio
async def test_merge_retries_with_fallback_on_405() -> None:
    """405 on the requested method → retry once with a permitted method."""
    svc = _service()
    _wire_merge(svc)
    call_merge = AsyncMock(
        side_effect=[
            _resp(_METHOD_NOT_ALLOWED, text="Squash merges are not allowed"),
            _resp(200),
        ]
    )
    _bind(svc, "_call_merge_api", call_merge)
    _bind(svc, "_first_allowed_merge_method", AsyncMock(return_value="merge"))

    target, commit = await svc.merge_pull_request(
        Path("/tmp/ws"), _PR, "squash", "roboco"
    )

    assert (target, commit) == ("main", "abc123")
    assert call_merge.await_count == 2  # noqa: PLR2004 -- initial + fallback retry
    # The retry used the permitted fallback method (5th positional arg).
    assert call_merge.await_args_list[1].args[4] == "merge"


@pytest.mark.asyncio
async def test_merge_no_retry_when_first_method_succeeds() -> None:
    """Requested method accepted → no repo lookup, no second merge call."""
    svc = _service()
    _wire_merge(svc)
    call_merge = AsyncMock(return_value=_resp(200))
    _bind(svc, "_call_merge_api", call_merge)
    first_allowed = AsyncMock(return_value="merge")
    _bind(svc, "_first_allowed_merge_method", first_allowed)

    target, _ = await svc.merge_pull_request(Path("/tmp/ws"), _PR, "squash", "roboco")

    assert target == "main"
    call_merge.assert_awaited_once()
    first_allowed.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_raises_when_no_permitted_fallback() -> None:
    """405 and no permitted fallback → the refusal still surfaces as GitError."""
    svc = _service()
    _wire_merge(svc)
    _bind(
        svc,
        "_call_merge_api",
        AsyncMock(return_value=_resp(_METHOD_NOT_ALLOWED, text="not allowed")),
    )
    _bind(svc, "_first_allowed_merge_method", AsyncMock(return_value=None))

    with pytest.raises(GitError, match="refused PR merge"):
        await svc.merge_pull_request(Path("/tmp/ws"), _PR, "squash", "roboco")

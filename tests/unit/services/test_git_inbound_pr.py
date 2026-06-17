"""GitService inbound-PR reads — list_open_prs + get_pr_diff (httpx fully mocked).

``list_open_prs`` is the discovery surface for the inbound-PR reviewer: it lists
ALL open PRs and normalizes each (number, url, title, head_ref, head_sha,
is_fork, user_login, author_association). ``get_pr_diff`` fetches a PR's unified
diff read-only. Both return safe empties ([] / "") on a missing token or any
GitHub error so the poll/claim never crash.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.git import GitService

_PR = 7


def _service() -> GitService:
    session = MagicMock()
    session.execute = AsyncMock()
    svc = GitService(session)
    object.__setattr__(svc, "_token_for_project", AsyncMock(return_value="tok"))
    return svc


def _resp(status_code: int, *, json_payload: Any = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300  # noqa: PLR2004
    resp.json.return_value = json_payload
    resp.text = text
    return resp


def _client(get_resp: MagicMock) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=get_resp)
    return client


def _patch_project() -> Any:
    fake = MagicMock()
    fake.get_by_slug = AsyncMock(
        return_value=MagicMock(git_url="https://github.com/acme/repo.git")
    )
    return patch("roboco.services.git.get_project_service", return_value=fake)


def _pr(*, number: int, head_full: str, login: str, assoc: str) -> dict[str, Any]:
    return {
        "number": number,
        "html_url": f"https://github.com/acme/repo/pull/{number}",
        "title": f"PR {number}",
        "head": {
            "ref": "feature-x",
            "sha": "deadbeef",
            "repo": {"full_name": head_full},
        },
        "user": {"login": login},
        "author_association": assoc,
    }


# ---------------------------------------------------------------------------
# list_open_prs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_open_prs_normalizes_and_flags_fork() -> None:
    svc = _service()
    payload = [
        _pr(number=7, head_full="forkowner/repo", login="corey", assoc="CONTRIBUTOR"),
        _pr(number=8, head_full="acme/repo", login="be-dev-1", assoc="MEMBER"),
    ]
    client = _client(_resp(200, json_payload=payload))
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await svc.list_open_prs("roboco")
    assert len(out) == 2  # noqa: PLR2004
    fork, internal = out
    assert fork == {
        "number": 7,
        "url": "https://github.com/acme/repo/pull/7",
        "title": "PR 7",
        "head_ref": "feature-x",
        "head_sha": "deadbeef",
        "is_fork": True,
        "user_login": "corey",
        "author_association": "CONTRIBUTOR",
    }
    # Same-repo head → not a fork.
    assert internal["is_fork"] is False
    assert internal["author_association"] == "MEMBER"


@pytest.mark.asyncio
async def test_list_open_prs_empty_on_missing_token() -> None:
    svc = _service()
    object.__setattr__(svc, "_token_for_project", AsyncMock(return_value=None))
    with _patch_project():
        assert await svc.list_open_prs("roboco") == []


@pytest.mark.asyncio
async def test_list_open_prs_empty_on_github_error() -> None:
    svc = _service()
    client = _client(_resp(403, text="forbidden"))
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        assert await svc.list_open_prs("roboco") == []


# ---------------------------------------------------------------------------
# get_pr_diff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pr_diff_returns_diff_text() -> None:
    svc = _service()
    diff = "diff --git a/x b/x\n+added line\n"
    client = _client(_resp(200, text=diff))
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await svc.get_pr_diff("roboco", _PR)
    assert out == diff
    call = client.get.await_args
    assert f"/pulls/{_PR}" in call.args[0]
    assert call.kwargs["headers"]["Accept"] == "application/vnd.github.v3.diff"


@pytest.mark.asyncio
async def test_get_pr_diff_empty_on_missing_token() -> None:
    svc = _service()
    object.__setattr__(svc, "_token_for_project", AsyncMock(return_value=None))
    with _patch_project():
        assert await svc.get_pr_diff("roboco", _PR) == ""


@pytest.mark.asyncio
async def test_get_pr_diff_empty_on_non_2xx() -> None:
    svc = _service()
    client = _client(_resp(404, text="not found"))
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        assert await svc.get_pr_diff("roboco", _PR) == ""


# ---------------------------------------------------------------------------
# get_latest_ci_conclusion — the self-heal CI telemetry signal
# ---------------------------------------------------------------------------


def _patch_project_ci() -> Any:
    fake = MagicMock()
    fake.get_by_slug = AsyncMock(
        return_value=MagicMock(
            git_url="https://github.com/acme/repo.git", default_branch="master"
        )
    )
    return patch("roboco.services.git.get_project_service", return_value=fake)


def _run(conclusion: str) -> dict[str, Any]:
    return {
        "conclusion": conclusion,
        "head_sha": "abc123",
        "html_url": "https://github.com/acme/repo/actions/runs/99",
        "name": "CI",
        "updated_at": "2026-06-17T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_get_latest_ci_conclusion_normalizes_and_requests_correctly() -> None:
    svc = _service()
    client = _client(_resp(200, json_payload={"workflow_runs": [_run("failure")]}))
    with (
        _patch_project_ci(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await svc.get_latest_ci_conclusion("roboco")
    assert out == {
        "conclusion": "failure",
        "head_sha": "abc123",
        "run_url": "https://github.com/acme/repo/actions/runs/99",
        "run_name": "CI",
        "branch": "master",
        "completed_at": "2026-06-17T00:00:00Z",
    }
    call = client.get.await_args
    assert call.args[0].endswith("/repos/acme/repo/actions/runs")
    assert call.kwargs["params"] == {
        "branch": "master",
        "status": "completed",
        "per_page": 1,
    }
    assert call.kwargs["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_get_latest_ci_conclusion_none_on_missing_token() -> None:
    svc = _service()
    object.__setattr__(svc, "_token_for_project", AsyncMock(return_value=None))
    with _patch_project_ci():
        assert await svc.get_latest_ci_conclusion("roboco") is None


@pytest.mark.asyncio
async def test_get_latest_ci_conclusion_none_on_github_error() -> None:
    svc = _service()
    client = _client(_resp(403, text="forbidden"))
    with (
        _patch_project_ci(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        assert await svc.get_latest_ci_conclusion("roboco") is None


@pytest.mark.asyncio
async def test_get_latest_ci_conclusion_none_when_no_runs() -> None:
    # A repo with no Actions runs (e.g. doesn't use GitHub Actions) → no signal.
    svc = _service()
    client = _client(_resp(200, json_payload={"workflow_runs": []}))
    with (
        _patch_project_ci(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        assert await svc.get_latest_ci_conclusion("roboco") is None


@pytest.mark.asyncio
async def test_get_latest_ci_conclusion_scopes_to_workflow() -> None:
    # With a workflow file given, hit the workflow-scoped endpoint (precise signal).
    svc = _service()
    client = _client(_resp(200, json_payload={"workflow_runs": [_run("success")]}))
    with (
        _patch_project_ci(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await svc.get_latest_ci_conclusion("roboco", workflow="ci.yml")
    assert out is not None
    assert out["conclusion"] == "success"
    assert client.get.await_args.args[0].endswith("/actions/workflows/ci.yml/runs")

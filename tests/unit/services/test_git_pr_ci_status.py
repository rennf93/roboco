"""GitService.get_pr_ci_status — the CI signal behind the pr_pass gate.

Reads GitHub check-runs on a PR's head SHA (falling back to list-workflows
when zero check-runs exist yet) and classifies the result into one of:
success, failure, pending, pending_not_scheduled, no_ci_configured, error.
Every unresolvable case is classified explicitly — a missing project/git_url/
token, or an unreachable/nonexistent repo or PR, is ``no_ci_configured``
(the guard passes through with an evidence stamp); a genuine GitHub API
failure on a real, reachable repo is ``error`` (the guard stays fail-closed).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from roboco.services.git import GitService

_PR = 42
_SHA = "deadbeefcafebabe0000111122223333aaaabbbb"
_HTTP_SUCCESS_RANGE = range(200, 300)


def _service() -> GitService:
    session = MagicMock()
    session.execute = AsyncMock()
    svc = GitService(session)
    object.__setattr__(svc, "_token_for_project", AsyncMock(return_value="tok"))
    return svc


def _resp(status_code: int, *, json_payload: Any = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = status_code in _HTTP_SUCCESS_RANGE
    resp.json.return_value = json_payload
    return resp


def _client(*get_responses: MagicMock) -> MagicMock:
    """A fake httpx.AsyncClient whose ``.get`` serves responses in call order —
    every ``async with httpx.AsyncClient(...) as client`` in the service reuses
    the SAME instance (the patch target returns it unconditionally), so a list
    ``side_effect`` lines up with the sequential PR-head / check-runs /
    workflows calls."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=list(get_responses))
    return client


def _patch_project() -> Any:
    fake = MagicMock()
    fake.get_by_slug = AsyncMock(
        return_value=MagicMock(git_url="https://github.com/acme/repo.git")
    )
    return patch("roboco.services.git.get_project_service", return_value=fake)


def _pr_head_resp() -> MagicMock:
    return _resp(200, json_payload={"head": {"sha": _SHA}})


def _check_run(name: str, *, status: str, conclusion: str | None) -> dict[str, Any]:
    return {"name": name, "status": status, "conclusion": conclusion}


# ---------------------------------------------------------------------------
# Six CI-guard branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_checks_green_is_success() -> None:
    checks = _resp(
        200,
        json_payload={
            "check_runs": [
                _check_run("lint", status="completed", conclusion="success"),
                _check_run("tests", status="completed", conclusion="neutral"),
            ]
        },
    )
    client = _client(_pr_head_resp(), checks)
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await _service().get_pr_ci_status("roboco", _PR)
    assert out == {"state": "success", "head_sha": _SHA}


@pytest.mark.asyncio
async def test_failing_check_names_it() -> None:
    checks = _resp(
        200,
        json_payload={
            "check_runs": [
                _check_run("lint", status="completed", conclusion="success"),
                _check_run("tests", status="completed", conclusion="failure"),
            ]
        },
    )
    client = _client(_pr_head_resp(), checks)
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await _service().get_pr_ci_status("roboco", _PR)
    assert out is not None
    assert out["state"] == "failure"
    assert out["failing_checks"] == ["tests"]
    assert out["head_sha"] == _SHA


@pytest.mark.asyncio
async def test_still_running_check_is_pending() -> None:
    checks = _resp(
        200,
        json_payload={
            "check_runs": [
                _check_run("lint", status="completed", conclusion="success"),
                _check_run("tests", status="in_progress", conclusion=None),
            ]
        },
    )
    client = _client(_pr_head_resp(), checks)
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await _service().get_pr_ci_status("roboco", _PR)
    assert out == {"state": "pending", "head_sha": _SHA}


@pytest.mark.asyncio
async def test_check_runs_api_error_is_error_state() -> None:
    checks = _resp(500, json_payload={"message": "internal error"})
    client = _client(_pr_head_resp(), checks)
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await _service().get_pr_ci_status("roboco", _PR)
    assert out == {"state": "error", "head_sha": _SHA}


@pytest.mark.asyncio
async def test_zero_checks_with_no_workflows_is_no_ci_configured() -> None:
    checks = _resp(200, json_payload={"check_runs": []})
    workflows = _resp(200, json_payload={"total_count": 0})
    client = _client(_pr_head_resp(), checks, workflows)
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await _service().get_pr_ci_status("roboco", _PR)
    assert out == {"state": "no_ci_configured", "head_sha": _SHA}


@pytest.mark.asyncio
async def test_zero_checks_with_workflows_configured_is_pending_not_scheduled() -> None:
    checks = _resp(200, json_payload={"check_runs": []})
    workflows = _resp(200, json_payload={"total_count": 3})
    client = _client(_pr_head_resp(), checks, workflows)
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await _service().get_pr_ci_status("roboco", _PR)
    assert out == {"state": "pending_not_scheduled", "head_sha": _SHA}


# ---------------------------------------------------------------------------
# Config gaps and an unreachable/nonexistent repo pass through cleanly as
# no_ci_configured (pr_pass stamps the evidence note, never mistaken for a
# CI signal)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_ci_configured_on_missing_token() -> None:
    svc = _service()
    object.__setattr__(svc, "_token_for_project", AsyncMock(return_value=None))
    with _patch_project():
        out = await svc.get_pr_ci_status("roboco", _PR)
    assert out == {"state": "no_ci_configured", "head_sha": None}


@pytest.mark.asyncio
async def test_no_ci_configured_on_missing_project() -> None:
    fake = MagicMock()
    fake.get_by_slug = AsyncMock(return_value=None)
    with patch("roboco.services.git.get_project_service", return_value=fake):
        out = await _service().get_pr_ci_status("roboco", _PR)
    assert out == {"state": "no_ci_configured", "head_sha": None}


@pytest.mark.asyncio
async def test_no_ci_configured_when_pr_lookup_404s() -> None:
    # The PR lookup itself 404s — the repo/PR doesn't exist or isn't reachable.
    pr_lookup = _resp(404, json_payload={"message": "not found"})
    client = _client(pr_lookup)
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await _service().get_pr_ci_status("roboco", _PR)
    assert out == {"state": "no_ci_configured", "head_sha": None}


@pytest.mark.asyncio
async def test_no_ci_configured_when_pr_lookup_unreachable() -> None:
    # A connection/network failure resolving the PR's head SHA — the repo is
    # unreachable, not just returning an error response.
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await _service().get_pr_ci_status("roboco", _PR)
    assert out == {"state": "no_ci_configured", "head_sha": None}


# ---------------------------------------------------------------------------
# A real, reachable repo's genuinely failing GitHub API call stays
# fail-closed (error), never conflated with the no_ci_configured cases above
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_when_pr_lookup_api_fails_on_real_repo() -> None:
    # The repo/project/token all resolve fine, but the PR-head-sha lookup
    # itself returns a genuine 5xx — this must stay fail-closed (error), not
    # be conflated with the unreachable/nonexistent-repo no_ci_configured case.
    pr_lookup = _resp(500, json_payload={"message": "internal error"})
    client = _client(pr_lookup)
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await _service().get_pr_ci_status("roboco", _PR)
    assert out == {"state": "error", "head_sha": None}


@pytest.mark.asyncio
async def test_error_when_pr_lookup_body_unparseable() -> None:
    pr_lookup = _resp(200, json_payload={"head": {}})  # missing "sha" key
    client = _client(pr_lookup)
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await _service().get_pr_ci_status("roboco", _PR)
    assert out == {"state": "error", "head_sha": None}


@pytest.mark.asyncio
async def test_workflows_api_error_after_zero_checks_is_error_state() -> None:
    checks = _resp(200, json_payload={"check_runs": []})
    workflows = _resp(503, json_payload={"message": "busy"})
    client = _client(_pr_head_resp(), checks, workflows)
    with (
        _patch_project(),
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
    ):
        out = await _service().get_pr_ci_status("roboco", _PR)
    assert out == {"state": "error", "head_sha": _SHA}

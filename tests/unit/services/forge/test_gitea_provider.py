"""GiteaProvider wire contract: token auth scheme, GitHub-shape adapters
(duplicate-PR 409→422, statuses→check_runs, combined-status→workflow_runs,
merge-method key mapping), and the deliberate Phase-2 postures (synthetic
zero workflows, unsupported server-side branch merge).

Uses httpx.MockTransport through the provider's own ``_send`` (the
``client=``-less path is exercised by monkeypatching ``httpx.AsyncClient``
to inject the transport — the same seam the git-service suite patches).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from roboco.services.forge.base import RepoRef
from roboco.services.forge.gitea import GiteaProvider, ShapedResponse

REF = RepoRef("acme", "widgets", host="gitea.example.com")


class _Recorder:
    def __init__(self, responder: Any) -> None:
        self.requests: list[httpx.Request] = []
        self._responder = responder

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)


def _patch_client(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    real_client = httpx.AsyncClient

    def _factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(recorder.handler))

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_auth_header_uses_token_scheme_and_api_v1_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json={}))
    _patch_client(monkeypatch, recorder)

    await GiteaProvider("gitea.example.com").get_pr(REF, "SECRET", 7)

    request = recorder.requests[0]
    assert request.headers["Authorization"] == "token SECRET"
    assert (
        str(request.url)
        == "https://gitea.example.com/api/v1/repos/acme/widgets/pulls/7"
    )


@pytest.mark.asyncio
async def test_create_pr_duplicate_409_reshapes_to_github_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(
        lambda _r: httpx.Response(
            409, text="pull request already exists for these targets"
        )
    )
    _patch_client(monkeypatch, recorder)

    resp = await GiteaProvider("gitea.example.com").create_pr(
        REF, "t", head="feat", base="main", title="T", body="B"
    )

    assert resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY
    assert "already exists" in resp.text


@pytest.mark.asyncio
async def test_merge_pr_posts_do_key(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json={}))
    _patch_client(monkeypatch, recorder)

    await GiteaProvider("gitea.example.com").merge_pr(
        REF, "t", 7, merge_method="squash"
    )

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path.endswith("/pulls/7/merge")
    assert b'"Do": "squash"' in request.content or b'"Do":"squash"' in request.content


@pytest.mark.asyncio
async def test_post_review_maps_approve_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json={}))
    _patch_client(monkeypatch, recorder)

    provider = GiteaProvider("gitea.example.com")
    await provider.post_review(REF, "t", 7, body="lgtm", event="APPROVE")
    await provider.post_review(REF, "t", 7, body="fix", event="REQUEST_CHANGES")

    assert b"APPROVED" in recorder.requests[0].content
    assert b"REQUEST_CHANGES" in recorder.requests[1].content


@pytest.mark.asyncio
async def test_list_pulls_filters_client_side_and_injects_association(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pulls = [
        {"number": 1, "head": {"ref": "feat-a"}, "base": {"ref": "main"}},
        {"number": 2, "head": {"ref": "feat-b"}, "base": {"ref": "main"}},
    ]
    recorder = _Recorder(lambda _r: httpx.Response(200, json=pulls))
    _patch_client(monkeypatch, recorder)

    resp = await GiteaProvider("gitea.example.com").list_pulls(
        REF, "t", head="feat-b", base="main"
    )

    selected = resp.json()
    assert [pr["number"] for pr in selected] == [2]
    assert selected[0]["author_association"] == "NONE"


@pytest.mark.asyncio
async def test_check_runs_reshaped_from_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = [
        {"id": 11, "status": "success", "context": "ci/build"},
        {"id": 12, "status": "pending", "context": "ci/test"},
        {"id": 13, "status": "error", "context": "ci/lint"},
    ]
    recorder = _Recorder(lambda _r: httpx.Response(200, json=statuses))
    _patch_client(monkeypatch, recorder)

    resp = await GiteaProvider("gitea.example.com").list_check_runs(
        REF, "t", "abc123", per_page=100
    )

    runs = resp.json()["check_runs"]
    assert runs[0] == {
        "id": 11,
        "name": "ci/build",
        "status": "completed",
        "conclusion": "success",
    }
    assert runs[1]["status"] == "in_progress"
    assert runs[1]["conclusion"] is None
    assert runs[2]["conclusion"] == "failure"


@pytest.mark.asyncio
async def test_ci_runs_reshaped_from_combined_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    combined = {"state": "failure", "sha": "abc123", "url": "https://x"}
    recorder = _Recorder(lambda _r: httpx.Response(200, json=combined))
    _patch_client(monkeypatch, recorder)

    resp = await GiteaProvider("gitea.example.com").list_ci_runs(
        REF, "t", workflow=None, branch="main", head_sha=None, per_page=5
    )

    runs = resp.json()["workflow_runs"]
    assert len(runs) == 1
    assert runs[0]["conclusion"] == "failure"
    assert runs[0]["head_sha"] == "abc123"


@pytest.mark.asyncio
async def test_pending_combined_status_yields_no_completed_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(
        lambda _r: httpx.Response(200, json={"state": "pending", "sha": "abc"})
    )
    _patch_client(monkeypatch, recorder)

    resp = await GiteaProvider("gitea.example.com").list_ci_runs(
        REF, "t", workflow=None, branch="main", head_sha=None, per_page=5
    )

    assert resp.json()["workflow_runs"] == []


@pytest.mark.asyncio
async def test_list_workflows_is_synthetic_zero() -> None:
    resp = await GiteaProvider("gitea.example.com").list_workflows(REF, "t", per_page=1)
    assert resp.is_success
    assert resp.json() == {"total_count": 0}


@pytest.mark.asyncio
async def test_merge_branch_is_shaped_not_implemented() -> None:
    resp = await GiteaProvider("gitea.example.com").merge_branch(
        REF, "t", base="stag", head="main", commit_message="cascade"
    )
    assert isinstance(resp, ShapedResponse)
    assert resp.status_code == httpx.codes.NOT_IMPLEMENTED


@pytest.mark.asyncio
async def test_get_repo_maps_merge_method_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_obj = {
        "full_name": "acme/widgets",
        "allow_merge_commits": False,
        "allow_rebase": False,
        "allow_squash_merge": True,
    }
    recorder = _Recorder(lambda _r: httpx.Response(200, json=repo_obj))
    _patch_client(monkeypatch, recorder)

    resp = await GiteaProvider("gitea.example.com").get_repo(REF, "t")

    shaped = resp.json()
    assert shaped["allow_merge_commit"] is False
    assert shaped["allow_rebase_merge"] is False
    assert shaped["allow_squash_merge"] is True


def test_parse_repo_ref_stamps_host() -> None:
    provider = GiteaProvider("gitea.example.com")
    ref = provider.parse_repo_ref("https://gitea.example.com/acme/widgets.git")
    assert ref == RepoRef("acme", "widgets", host="gitea.example.com")


@pytest.mark.asyncio
async def test_ensure_label_prefixes_hash_on_color(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(201, json={}))
    _patch_client(monkeypatch, recorder)

    await GiteaProvider("gitea.example.com").ensure_label(REF, "t", "root", "8250df")

    assert b"#8250df" in recorder.requests[0].content

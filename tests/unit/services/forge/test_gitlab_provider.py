"""GitLabProvider wire contract: Bearer auth + urlencoded project path,
MR→PR shape adaptation (iid→number, source/target_branch→head/base, merged
bool), payload-key translation (create_pr duplicate 409→422, update_pr
close→state_event), squash-flag merge, approve-vs-note review routing, diff
reassembly, pipelines→workflow_runs / statuses→check_runs CI classification,
merge-method mapping, and the deliberate Phase-3 synthetic postures
(request_reviewers, create_org_repo, merge_branch).

Uses httpx.MockTransport through the provider's own ``_send`` — same seam
``test_gitea_provider.py`` exercises.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from roboco.services.forge.base import RepoRef
from roboco.services.forge.gitlab import GitLabProvider, ShapedResponse

if TYPE_CHECKING:
    from collections.abc import Callable

REF = RepoRef("group/sub/proj", "", host="gitlab.example.com")

_MR_IID = 3
_DIFF_PAGE_CAP = 3


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
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(recorder.handler))

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_auth_header_uses_bearer_and_urlencoded_subgroup_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json={}))
    _patch_client(monkeypatch, recorder)

    await GitLabProvider("gitlab.example.com").get_pr(REF, "SECRET", 7)

    request = recorder.requests[0]
    assert request.headers["Authorization"] == "Bearer SECRET"
    assert (
        str(request.url)
        == "https://gitlab.example.com/api/v4/projects/group%2Fsub%2Fproj/merge_requests/7"
    )


def test_parse_repo_ref_subgroup_path_stamps_host() -> None:
    provider = GitLabProvider("gitlab.example.com")
    ref = provider.parse_repo_ref("https://gitlab.example.com/group/sub/proj.git")
    assert ref == RepoRef("group/sub/proj", "", host="gitlab.example.com")


def test_parse_repo_ref_rejects_single_segment_path() -> None:
    provider = GitLabProvider("gitlab.example.com")
    with pytest.raises(Exception, match="namespace/project"):
        provider.parse_repo_ref("https://gitlab.example.com/onlyproject.git")


@pytest.mark.asyncio
async def test_list_pulls_maps_state_and_filters_natively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mrs = [
        {
            "iid": _MR_IID,
            "web_url": "https://gitlab.example.com/group/sub/proj/-/merge_requests/3",
            "title": "Feature",
            "state": "opened",
            "source_branch": "feat-a",
            "target_branch": "main",
            "sha": "abc123",
            "author": {"username": "renzo"},
        }
    ]
    recorder = _Recorder(lambda _r: httpx.Response(200, json=mrs))
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").list_pulls(
        REF, "t", head="feat-a", base="main"
    )

    request = recorder.requests[0]
    assert request.url.params["state"] == "opened"
    assert request.url.params["source_branch"] == "feat-a"
    assert request.url.params["target_branch"] == "main"

    shaped = resp.json()[0]
    assert shaped["number"] == _MR_IID
    assert shaped["html_url"].endswith("/merge_requests/3")
    assert shaped["state"] == "open"
    assert shaped["merged"] is False
    assert shaped["head"] == {
        "ref": "feat-a",
        "sha": "abc123",
        "repo": {"full_name": "group/sub/proj"},
    }
    assert shaped["base"] == {"ref": "main"}
    assert shaped["user"] == {"login": "renzo"}
    assert shaped["author_association"] == "NONE"


@pytest.mark.asyncio
async def test_get_pr_adapts_merged_state(monkeypatch: pytest.MonkeyPatch) -> None:
    mr = {
        "iid": 9,
        "web_url": "https://gitlab.example.com/x",
        "title": "T",
        "state": "merged",
        "source_branch": "feat",
        "target_branch": "main",
        "sha": "deadbeef",
        "author": {},
    }
    recorder = _Recorder(lambda _r: httpx.Response(200, json=mr))
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").get_pr(REF, "t", 9)

    shaped = resp.json()
    assert shaped["state"] == "closed"
    assert shaped["merged"] is True
    assert shaped["user"] == {"login": ""}


@pytest.mark.asyncio
async def test_create_pr_translates_payload_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(
        lambda _r: httpx.Response(
            201,
            json={
                "iid": 1,
                "web_url": "https://x",
                "title": "T",
                "state": "opened",
                "source_branch": "feat",
                "target_branch": "main",
                "sha": "s",
                "author": {"username": "bot"},
            },
        )
    )
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").create_pr(
        REF, "t", head="feat", base="main", title="T", body="B"
    )

    request = recorder.requests[0]
    assert (
        b'"source_branch": "feat"' in request.content
        or b'"source_branch":"feat"' in request.content
    )
    assert (
        b'"target_branch": "main"' in request.content
        or b'"target_branch":"main"' in request.content
    )
    assert (
        b'"description": "B"' in request.content
        or b'"description":"B"' in request.content
    )
    assert resp.json()["number"] == 1


@pytest.mark.asyncio
async def test_create_pr_duplicate_409_reshapes_to_github_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(
        lambda _r: httpx.Response(409, text="Another open merge request already exists")
    )
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").create_pr(
        REF, "t", head="feat", base="main", title="T", body="B"
    )

    assert resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY
    assert "already exists" in resp.text


@pytest.mark.asyncio
async def test_update_pr_close_maps_to_state_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json={}))
    _patch_client(monkeypatch, recorder)

    await GitLabProvider("gitlab.example.com").update_pr(
        REF, "t", 5, payload={"state": "closed", "body": "why"}
    )

    request = recorder.requests[0]
    assert request.method == "PUT"
    assert (
        b'"state_event": "close"' in request.content
        or b'"state_event":"close"' in request.content
    )
    assert (
        b'"description": "why"' in request.content
        or b'"description":"why"' in request.content
    )
    assert b"state_event" in request.content
    assert b'"state":' not in request.content


@pytest.mark.asyncio
async def test_merge_pr_sends_squash_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json={}))
    _patch_client(monkeypatch, recorder)

    await GitLabProvider("gitlab.example.com").merge_pr(
        REF, "t", 5, merge_method="squash"
    )

    request = recorder.requests[0]
    assert request.method == "PUT"
    assert request.url.path.endswith("/merge_requests/5/merge")
    assert b'"squash": true' in request.content or b'"squash":true' in request.content


@pytest.mark.asyncio
async def test_merge_pr_no_squash_when_method_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json={}))
    _patch_client(monkeypatch, recorder)

    await GitLabProvider("gitlab.example.com").merge_pr(
        REF, "t", 5, merge_method="merge"
    )

    assert b'"squash": false' in recorder.requests[0].content or (
        b'"squash":false' in recorder.requests[0].content
    )


@pytest.mark.asyncio
async def test_post_review_approve_hits_approve_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json={}))
    _patch_client(monkeypatch, recorder)

    await GitLabProvider("gitlab.example.com").post_review(
        REF, "t", 5, body="lgtm", event="APPROVE"
    )

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path.endswith("/merge_requests/5/approve")


@pytest.mark.asyncio
async def test_post_review_request_changes_posts_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json={}))
    _patch_client(monkeypatch, recorder)

    await GitLabProvider("gitlab.example.com").post_review(
        REF, "t", 5, body="please fix X", event="REQUEST_CHANGES"
    )

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path.endswith("/merge_requests/5/notes")
    assert b"please fix X" in request.content


@pytest.mark.asyncio
async def test_get_pr_diff_reassembles_unified_text_single_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = [
        {"old_path": "a.py", "new_path": "a.py", "diff": "@@ -1 +1 @@\n-x\n+y\n"},
        {"old_path": "b.py", "new_path": "b.py", "diff": "@@ -1 +1 @@\n-p\n+q\n"},
    ]
    recorder = _Recorder(lambda _r: httpx.Response(200, json=page))
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").get_pr_diff(REF, "t", 9)

    assert len(recorder.requests) == 1
    assert "diff --git a/a.py b/a.py" in resp.text
    assert "diff --git a/b.py b/b.py" in resp.text
    assert "@@ -1 +1 @@\n-x\n+y\n" in resp.text


@pytest.mark.asyncio
async def test_get_pr_diff_paginates_full_pages_and_caps_at_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full_page = [
        {"old_path": f"f{i}.py", "new_path": f"f{i}.py", "diff": "d\n"}
        for i in range(100)
    ]

    def _responder(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=full_page)

    recorder = _Recorder(_responder)
    _patch_client(monkeypatch, recorder)

    await GitLabProvider("gitlab.example.com").get_pr_diff(REF, "t", 9)

    # 3 full pages fetched, then the loop stops without a 4th request.
    assert len(recorder.requests) == _DIFF_PAGE_CAP
    pages = [r.url.params.get("page") for r in recorder.requests]
    assert pages == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_list_ci_runs_reports_newest_settled_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipelines = [
        {"status": "success", "sha": "abc", "web_url": "https://x", "updated_at": "t"},
        {"status": "failed", "sha": "old", "web_url": "https://y", "updated_at": "t2"},
    ]
    recorder = _Recorder(lambda _r: httpx.Response(200, json=pipelines))
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").list_ci_runs(
        REF, "t", workflow=None, branch="main", head_sha=None, per_page=5
    )

    runs = resp.json()["workflow_runs"]
    assert len(runs) == 1
    assert runs[0]["head_sha"] == "abc"
    assert runs[0]["conclusion"] == "success"
    assert runs[0]["name"] == "pipeline"


@pytest.mark.asyncio
async def test_list_ci_runs_skips_unsettled_leading_pipelines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipelines = [
        {"status": "running", "sha": "new"},
        {"status": "success", "sha": "prior"},
    ]
    recorder = _Recorder(lambda _r: httpx.Response(200, json=pipelines))
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").list_ci_runs(
        REF, "t", workflow=None, branch="main", head_sha=None, per_page=5
    )

    runs = resp.json()["workflow_runs"]
    assert len(runs) == 1
    assert runs[0]["head_sha"] == "prior"


@pytest.mark.asyncio
async def test_list_ci_runs_none_settled_yields_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json=[{"status": "running"}]))
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").list_ci_runs(
        REF, "t", workflow=None, branch="main", head_sha=None, per_page=5
    )

    assert resp.json()["workflow_runs"] == []


@pytest.mark.asyncio
async def test_check_runs_reshaped_from_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = [
        {"id": 1, "status": "success", "name": "build"},
        {"id": 2, "status": "running", "name": "test"},
        {"id": 3, "status": "failed", "name": "lint"},
        {"id": 4, "status": "canceled", "name": "deploy"},
    ]
    recorder = _Recorder(lambda _r: httpx.Response(200, json=statuses))
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").list_check_runs(
        REF, "t", "abc123", per_page=100
    )

    runs = resp.json()["check_runs"]
    assert runs[0] == {
        "id": 1,
        "name": "build",
        "status": "completed",
        "conclusion": "success",
    }
    assert runs[1]["status"] == "in_progress"
    assert runs[1]["conclusion"] is None
    assert runs[2]["conclusion"] == "failure"
    assert runs[3]["conclusion"] == "failure"


@pytest.mark.asyncio
async def test_list_workflows_reports_total_count_from_pipelines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json=[{"id": 1}]))
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").list_workflows(
        REF, "t", per_page=1
    )

    assert resp.json() == {"total_count": 1}


@pytest.mark.asyncio
async def test_list_workflows_zero_when_no_pipelines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json=[]))
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").list_workflows(
        REF, "t", per_page=1
    )

    assert resp.json() == {"total_count": 0}


@pytest.mark.asyncio
async def test_get_repo_maps_merge_method_and_squash_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_obj = {
        "path_with_namespace": "group/sub/proj",
        "web_url": "https://gitlab.example.com/group/sub/proj",
        "http_url_to_repo": "https://gitlab.example.com/group/sub/proj.git",
        "squash_option": "never",
        "merge_method": "ff",
    }
    recorder = _Recorder(lambda _r: httpx.Response(200, json=repo_obj))
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").get_repo(REF, "t")

    shaped = resp.json()
    assert shaped["full_name"] == "group/sub/proj"
    assert shaped["html_url"] == "https://gitlab.example.com/group/sub/proj"
    assert shaped["clone_url"] == "https://gitlab.example.com/group/sub/proj.git"
    assert shaped["allow_squash_merge"] is False
    assert shaped["allow_merge_commit"] is False
    assert shaped["allow_rebase_merge"] is True


@pytest.mark.asyncio
async def test_get_repo_defaults_when_settings_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json={}))
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").get_repo(REF, "t")

    shaped = resp.json()
    assert shaped["allow_squash_merge"] is True
    assert shaped["allow_merge_commit"] is True
    assert shaped["allow_rebase_merge"] is False


@pytest.mark.asyncio
async def test_ensure_label_prefixes_hash_on_color(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(201, json={}))
    _patch_client(monkeypatch, recorder)

    await GitLabProvider("gitlab.example.com").ensure_label(REF, "t", "root", "8250df")

    assert b"#8250df" in recorder.requests[0].content


@pytest.mark.asyncio
async def test_add_labels_uses_add_labels_key(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(200, json={}))
    _patch_client(monkeypatch, recorder)

    await GitLabProvider("gitlab.example.com").add_labels(REF, "t", 5, ["a", "b"])

    request = recorder.requests[0]
    assert request.method == "PUT"
    assert (
        b'"add_labels": "a,b"' in request.content
        or b'"add_labels":"a,b"' in request.content
    )


@pytest.mark.asyncio
async def test_delete_branch_ref_urlencodes_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(204))
    _patch_client(monkeypatch, recorder)

    await GitLabProvider("gitlab.example.com").delete_branch_ref(
        REF, "t", "feature/backend/ABC12345"
    )

    request = recorder.requests[0]
    assert request.method == "DELETE"
    assert "feature%2Fbackend%2FABC12345" in str(request.url)


@pytest.mark.asyncio
async def test_create_issue_comment_posts_note(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(lambda _r: httpx.Response(201, json={}))
    _patch_client(monkeypatch, recorder)

    await GitLabProvider("gitlab.example.com").create_issue_comment(REF, "t", 5, "hi")

    request = recorder.requests[0]
    assert request.url.path.endswith("/merge_requests/5/notes")
    assert b"hi" in request.content


@pytest.mark.asyncio
async def test_create_release_shapes_html_url_from_links_self(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(
        lambda _r: httpx.Response(
            201,
            json={
                "tag_name": "v1.0.0",
                "_links": {
                    "self": "https://gitlab.example.com/group/sub/proj/-/releases/v1.0.0"
                },
            },
        )
    )
    _patch_client(monkeypatch, recorder)

    resp = await GitLabProvider("gitlab.example.com").create_release(
        REF,
        "t",
        tag_name="v1.0.0",
        name="v1.0.0",
        body="notes",
        target_commitish="main",
    )

    assert resp.json()["html_url"] == (
        "https://gitlab.example.com/group/sub/proj/-/releases/v1.0.0"
    )
    request = recorder.requests[0]
    assert (
        b'"description": "notes"' in request.content
        or b'"description":"notes"' in request.content
    )


@pytest.mark.asyncio
async def test_request_reviewers_is_synthetic_skip() -> None:
    resp = await GitLabProvider("gitlab.example.com").request_reviewers(
        REF, "t", 5, ["renzo"]
    )
    assert isinstance(resp, ShapedResponse)
    assert resp.is_success
    assert "skipped" in resp.json()


@pytest.mark.asyncio
async def test_merge_branch_is_shaped_not_implemented() -> None:
    resp = await GitLabProvider("gitlab.example.com").merge_branch(
        REF, "t", base="stag", head="main", commit_message="cascade"
    )
    assert isinstance(resp, ShapedResponse)
    assert resp.status_code == httpx.codes.NOT_IMPLEMENTED


@pytest.mark.asyncio
async def test_create_org_repo_is_synthetic_501() -> None:
    resp = await GitLabProvider("gitlab.example.com").create_org_repo(
        "t", "acme", name="widgets", description="", private=True, auto_init=True
    )
    assert isinstance(resp, ShapedResponse)
    assert resp.status_code == httpx.codes.NOT_IMPLEMENTED
    assert "Phase 4" in resp.json()["message"]

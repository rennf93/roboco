"""Unit tests for GitService rebase conflict-state handling.

Pins the three critical control-flow branches of ``rebase_onto_base``:

1. **Success** — the underlying ``git rebase`` exits 0 → method returns a
   non-conflict result dict and never calls ``git rebase --abort``.
2. **Conflict** — ``git rebase`` exits non-zero → method calls
   ``git diff --name-only --diff-filter=U`` to collect conflicted files,
   calls ``git rebase --abort`` to restore the workspace, and returns a
   conflict result dict.
3. **Resilience** — both ``git rebase`` and ``git rebase --abort`` exit
   non-zero (e.g. abort fails mid-stream).  The method must still return
   the conflict dict without propagating an exception, because both are
   invoked with ``check=False``.

All tests mock ``_run_git`` at the service-method level using
``AsyncMock`` with a ``side_effect`` list so each awaited call consumes
the next pre-configured result in order.

Also covers the ``rebase()`` safety gate added by the git-schema cleanup
task: rebasing onto or from a protected branch (master/main) is rejected
with a service-layer ``ValidationError`` before any git command runs.

Also covers:
* ``pull()`` dirty-tree and diverged-branch ``ValidationError`` gates.
* ``pull()`` success path.
* ``GitRebaseRequest.target_branch`` Pydantic field validator.
* Route-level role gate: DEVELOPER → 403, CELL_PM → 200.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pydantic
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.git import router as git_router
from roboco.api.schemas.git import GitRebaseRequest
from roboco.models.base import AgentRole
from roboco.models.permissions import AgentContext
from roboco.services.base import ValidationError
from roboco.services.git import GitService

_HTTP_200 = 200
_HTTP_403 = 403

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEAD = "feature/backend/root--task"
_BASE = "feature/backend/root"
_WORKSPACE = Path("/tmp/fake-ws")
_TOKEN = "ghp_fake"


def _git_service() -> GitService:
    """Instantiate GitService without a real DB session."""
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()  # silence warning/info calls
    # A placeholder — only touched (as an opaque arg to a patched
    # get_project_service) by the protected_branches union tests below.
    svc.session = MagicMock()
    return svc


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    """Minimal subprocess result stand-in."""
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ---------------------------------------------------------------------------
# Test 1 — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_path_returns_rebased_and_does_not_call_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When git rebase exits 0 the method returns a non-conflict result and
    never invokes ``git rebase --abort``.

    Call sequence for the success path (rebase OK, 2 unique commits):
      [0] status --porcelain          ← clean (the H8 dirty-tree gate)
      [1] fetch origin
      [2] checkout HEAD branch
      [3] reset --hard origin/HEAD
      [4] rebase origin/BASE          ← exits 0
      [5] rev-list --count            ← returns "2"
      [6] push --force-with-lease     ← pushes the rebased branch
    """
    run = AsyncMock(
        side_effect=[
            _result(stdout=""),  # [0] status --porcelain → clean
            _result(),  # [1] fetch
            _result(),  # [2] checkout
            _result(),  # [3] reset
            _result(),  # [4] rebase ← success
            _result(stdout="2\n"),  # [5] rev-list
            _result(),  # [6] push
        ]
    )
    monkeypatch.setattr(GitService, "_run_git", run)

    svc = _git_service()
    result = await svc.rebase_onto_base(
        _WORKSPACE,
        head_branch=_HEAD,
        base_branch=_BASE,
        git_token=_TOKEN,
    )

    assert result == {"status": "rebased", "unique_commits": 2}

    # Verify abort was never called
    abort_call = call(_WORKSPACE, ["rebase", "--abort"], check=False)
    assert abort_call not in run.call_args_list, (
        "git rebase --abort must NOT be called on a clean rebase"
    )


# ---------------------------------------------------------------------------
# Test 2 — conflict path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conflict_path_calls_diff_then_abort_and_returns_conflict_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When git rebase exits non-zero the method:

    * calls ``git diff --name-only --diff-filter=U`` to identify conflicted files,
    * calls ``git rebase --abort`` to restore the workspace,
    * returns ``{"status": "conflicts", "files": [<conflicted files>]}``.

    Call sequence:
      [0] status --porcelain          ← clean (the H8 dirty-tree gate)
      [1] fetch origin
      [2] checkout HEAD branch
      [3] reset --hard origin/HEAD
      [4] rebase origin/BASE          ← exits 1 (conflict)
      [5] diff --name-only            ← lists conflicted files
      [6] rebase --abort              ← exits 0
    """
    run = AsyncMock(
        side_effect=[
            _result(stdout=""),  # [0] status --porcelain → clean
            _result(),  # [1] fetch
            _result(),  # [2] checkout
            _result(),  # [3] reset
            _result(returncode=1),  # [4] rebase ← conflict
            _result(stdout="src/a.py\nsrc/b.py\n"),  # [5] diff
            _result(),  # [6] rebase --abort
        ]
    )
    monkeypatch.setattr(GitService, "_run_git", run)

    svc = _git_service()
    result = await svc.rebase_onto_base(
        _WORKSPACE,
        head_branch=_HEAD,
        base_branch=_BASE,
        git_token=_TOKEN,
    )

    assert result == {"status": "conflicts", "files": ["src/a.py", "src/b.py"]}

    # Verify the diff call was made with the correct flags
    diff_call = call(
        _WORKSPACE,
        ["diff", "--name-only", "--diff-filter=U"],
        check=False,
    )
    assert diff_call in run.call_args_list, (
        "git diff --name-only --diff-filter=U must be called to collect conflicted"
        " files"
    )

    # Verify abort was called
    abort_call = call(_WORKSPACE, ["rebase", "--abort"], check=False)
    assert abort_call in run.call_args_list, (
        "git rebase --abort must be called to restore the workspace after a conflict"
    )


# ---------------------------------------------------------------------------
# Test 3 — resilience: both rebase and abort exit non-zero
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resilience_when_both_rebase_and_abort_fail_returns_conflict_no_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``git rebase`` exits non-zero AND ``git rebase --abort`` also
    exits non-zero, the method must still return a conflict result dict
    without raising an exception.

    Both are called with ``check=False`` so a non-zero exit code from
    either command produces a result object (not a raised exception).

    Call sequence:
      [0] status --porcelain          ← clean (the H8 dirty-tree gate)
      [1] fetch origin
      [2] checkout HEAD branch
      [3] reset --hard origin/HEAD
      [4] rebase origin/BASE          ← exits 1 (conflict)
      [5] diff --name-only            ← lists conflicted files
      [6] rebase --abort              ← exits 1 (abort also fails)
    """
    run = AsyncMock(
        side_effect=[
            _result(stdout=""),  # [0] status --porcelain → clean
            _result(),  # [1] fetch
            _result(),  # [2] checkout
            _result(),  # [3] reset
            _result(returncode=1),  # [4] rebase ← conflict
            _result(stdout="src/conflict.py\n"),  # [5] diff
            _result(returncode=1),  # [6] rebase --abort ← also fails
        ]
    )
    monkeypatch.setattr(GitService, "_run_git", run)

    svc = _git_service()

    # Must not raise even though both rebase and abort return non-zero
    result = await svc.rebase_onto_base(
        _WORKSPACE,
        head_branch=_HEAD,
        base_branch=_BASE,
        git_token=_TOKEN,
    )

    assert result == {"status": "conflicts", "files": ["src/conflict.py"]}


# ---------------------------------------------------------------------------
# Safety gate tests for rebase() — protected-branch guard
# ---------------------------------------------------------------------------
# These test the service-layer ``rebase()`` method (the workspace-scoped API
# endpoint helper), NOT ``rebase_onto_base()`` (the internal gateway helper).
# The guard runs BEFORE any git command, so no ``_run_git`` mock is needed
# for target-branch cases; the head-branch case requires a stubbed
# ``get_current_branch``.


@pytest.mark.asyncio
async def test_rebase_raises_validation_error_when_target_is_master() -> None:
    """rebase() must raise ValidationError for target_branch='master'."""
    svc = _git_service()
    with pytest.raises(ValidationError, match="REBASE_FORBIDDEN"):
        await svc.rebase(_WORKSPACE, "master")


@pytest.mark.asyncio
async def test_rebase_raises_validation_error_when_target_is_main() -> None:
    """rebase() must raise ValidationError for target_branch='main'."""
    svc = _git_service()
    with pytest.raises(ValidationError, match="REBASE_FORBIDDEN"):
        await svc.rebase(_WORKSPACE, "main")


@pytest.mark.asyncio
async def test_rebase_raises_validation_error_when_head_branch_is_master(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rebase() must raise ValidationError when HEAD is 'master'.

    The target-branch check passes (we pass a safe target), but the
    head-branch guard fires when get_current_branch returns 'master'.
    """
    monkeypatch.setattr(
        GitService,
        "get_current_branch",
        AsyncMock(return_value="master"),
    )
    svc = _git_service()
    with pytest.raises(ValidationError, match="REBASE_FORBIDDEN"):
        await svc.rebase(_WORKSPACE, "feature/backend/some-task")


@pytest.mark.asyncio
async def test_rebase_raises_validation_error_when_head_branch_is_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rebase() must raise ValidationError when HEAD is 'main'."""
    monkeypatch.setattr(
        GitService,
        "get_current_branch",
        AsyncMock(return_value="main"),
    )
    svc = _git_service()
    with pytest.raises(ValidationError, match="REBASE_FORBIDDEN"):
        await svc.rebase(_WORKSPACE, "feature/backend/some-task")


# ---------------------------------------------------------------------------
# rebase() — projects.protected_branches UNION (2026-07-22 follow-up)
# ---------------------------------------------------------------------------
# rebase()'s hardcoded {"master", "main"} refusal is unioned with the
# project's own declared protected_branches when project_slug is given. The
# union can only ADD refusals: no project_slug, an unresolvable project, or
# an emptied field must reproduce the exact hardcoded-only behavior above.


def _project_service_returning(project: MagicMock) -> MagicMock:
    svc = MagicMock()
    svc.get_by_slug = AsyncMock(return_value=project)
    return svc


@pytest.mark.asyncio
async def test_rebase_raises_for_project_declared_protected_target_branch() -> None:
    """A custom protected branch (not master/main) is refused as a rebase
    target when the project declares it, before any git command runs."""
    svc = _git_service()
    project = MagicMock(protected_branches=["release"])
    with (
        patch(
            "roboco.services.git.get_project_service",
            return_value=_project_service_returning(project),
        ),
        pytest.raises(ValidationError, match="REBASE_FORBIDDEN"),
    ):
        await svc.rebase(_WORKSPACE, "release", "acme-repo")


@pytest.mark.asyncio
async def test_rebase_allows_target_not_in_projects_protected_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target that isn't master/main AND isn't in the project's declared
    list rebases normally — the union doesn't become deny-by-default."""
    run = AsyncMock(return_value=_result())
    monkeypatch.setattr(GitService, "_run_git", run)
    monkeypatch.setattr(
        GitService, "get_current_branch", AsyncMock(return_value="feature/x")
    )
    svc = _git_service()
    project = MagicMock(protected_branches=["release"])
    with patch(
        "roboco.services.git.get_project_service",
        return_value=_project_service_returning(project),
    ):
        conflict, files = await svc.rebase(
            _WORKSPACE, "feature/backend/some-task", "acme-repo"
        )
    assert (conflict, files) == (False, [])


@pytest.mark.asyncio
async def test_rebase_empty_protected_branches_matches_hardcoded_only_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty protected_branches field degrades to exactly the prior
    master/main-only behavior — no extra refusal is invented."""
    run = AsyncMock(return_value=_result())
    monkeypatch.setattr(GitService, "_run_git", run)
    monkeypatch.setattr(
        GitService, "get_current_branch", AsyncMock(return_value="feature/x")
    )
    svc = _git_service()
    project = MagicMock(protected_branches=[])
    with patch(
        "roboco.services.git.get_project_service",
        return_value=_project_service_returning(project),
    ):
        conflict, files = await svc.rebase(
            _WORKSPACE, "feature/backend/some-task", "acme-repo"
        )
    assert (conflict, files) == (False, [])


@pytest.mark.asyncio
async def test_rebase_no_project_slug_matches_hardcoded_only_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting project_slug entirely (legacy call shape) never touches the
    project service and behaves byte-for-byte like before this change."""
    run = AsyncMock(return_value=_result())
    monkeypatch.setattr(GitService, "_run_git", run)
    monkeypatch.setattr(
        GitService, "get_current_branch", AsyncMock(return_value="feature/x")
    )
    svc = _git_service()
    with patch("roboco.services.git.get_project_service") as get_project_service:
        conflict, files = await svc.rebase(_WORKSPACE, "feature/backend/some-task")
    assert (conflict, files) == (False, [])
    get_project_service.assert_not_called()


@pytest.mark.asyncio
async def test_rebase_matches_stripped_target_case_sensitively() -> None:
    """Stored entries are stripped defensively, but matching stays
    case-sensitive: a differently-cased target is NOT refused by a stored
    ' Release '."""
    project = MagicMock(protected_branches=[" Release "])

    svc = _git_service()
    with (
        patch(
            "roboco.services.git.get_project_service",
            return_value=_project_service_returning(project),
        ),
        pytest.raises(ValidationError, match="REBASE_FORBIDDEN"),
    ):
        await svc.rebase(_WORKSPACE, "Release", "acme-repo")


@pytest.mark.asyncio
async def test_rebase_union_never_collapses_hardcoded_floor_to_project_list_only() -> (
    None
):
    """A project declaring its OWN protected_branches (e.g. ["release"]) must
    NOT replace the hardcoded master/main refusal — the union is additive,
    never a substitution. Both master and main stay refused regardless of
    what the project's list contains."""
    project = MagicMock(protected_branches=["release"])

    svc = _git_service()
    with (
        patch(
            "roboco.services.git.get_project_service",
            return_value=_project_service_returning(project),
        ),
        pytest.raises(ValidationError, match="REBASE_FORBIDDEN"),
    ):
        await svc.rebase(_WORKSPACE, "master", "acme-repo")

    svc2 = _git_service()
    with (
        patch(
            "roboco.services.git.get_project_service",
            return_value=_project_service_returning(project),
        ),
        pytest.raises(ValidationError, match="REBASE_FORBIDDEN"),
    ):
        await svc2.rebase(_WORKSPACE, "main", "acme-repo")


# ---------------------------------------------------------------------------
# sync_task_branch() — protected HEAD branch guard (2026-07-22 follow-up)
# ---------------------------------------------------------------------------
# sync_branch force-pushes (with lease) the task's OWN branch_name, not the
# base — so the guard that matters here is on the HEAD, not the base (the
# choreographer's _sync_base_refused already guards the base and is
# untouched). A branch_name that IS master/main or one of the project's
# declared protected_branches means branch_name was mis-set; refuse before
# any workspace/rebase work.


def _sync_task(branch_name: str) -> MagicMock:
    # assigned_to=None so _resolve_workspace_agent_id (no actor_agent_id
    # passed) falls through to its None default instead of trying to parse
    # an auto-generated MagicMock attribute as a UUID.
    return MagicMock(id=uuid4(), branch_name=branch_name, assigned_to=None)


def _patch_sync_plumbing(
    monkeypatch: pytest.MonkeyPatch, project: MagicMock
) -> AsyncMock:
    """Stub every collaborator sync_task_branch calls AFTER the HEAD guard,
    so a test that reaches them proves the guard let a normal branch through
    without actually touching a filesystem or running git. Returns the
    rebase_onto_base mock so callers can assert on it."""
    monkeypatch.setattr(
        GitService, "_project_for_task", AsyncMock(return_value=project)
    )
    # _protected_branches_for (called from the new HEAD guard) resolves the
    # project independently via get_project_service, not _project_for_task.
    monkeypatch.setattr(
        "roboco.services.git.get_project_service",
        lambda _session: _project_service_returning(project),
    )
    monkeypatch.setattr(
        GitService, "get_workspace", AsyncMock(return_value=Path("/tmp/clone"))
    )
    monkeypatch.setattr(
        GitService, "_get_project_token_or_raise", AsyncMock(return_value="tok")
    )
    monkeypatch.setattr(GitService, "_ensure_worktree_for_commit", AsyncMock())
    rebase_onto_base = AsyncMock(
        return_value={"status": "rebased", "unique_commits": 1}
    )
    monkeypatch.setattr(GitService, "rebase_onto_base", rebase_onto_base)
    return rebase_onto_base


@pytest.mark.asyncio
async def test_sync_task_branch_refuses_project_declared_protected_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task whose branch_name IS a project-declared protected branch is
    refused before any workspace/rebase work runs."""
    project = MagicMock(slug="acme-repo", protected_branches=["release"])
    rebase_onto_base = _patch_sync_plumbing(monkeypatch, project)
    svc = _git_service()
    task = _sync_task("release")

    with pytest.raises(ValidationError, match="REBASE_FORBIDDEN"):
        await svc.sync_task_branch(task, base_branch="feature/backend/parent")

    rebase_onto_base.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_task_branch_allows_normal_head_unaffected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal task branch_name (not in the project's protected list) syncs
    through exactly as before — the guard doesn't become deny-by-default."""
    project = MagicMock(slug="acme-repo", protected_branches=["release"])
    rebase_onto_base = _patch_sync_plumbing(monkeypatch, project)
    svc = _git_service()
    task = _sync_task("feature/backend/abc12345")

    result = await svc.sync_task_branch(task, base_branch="feature/backend/parent")

    assert result == {"status": "rebased", "unique_commits": 1}
    rebase_onto_base.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_task_branch_empty_protected_branches_matches_current_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty (or null) protected_branches field degrades to exactly the
    prior master/main-only behavior — a normal branch still syncs fine."""
    project = MagicMock(slug="acme-repo", protected_branches=[])
    rebase_onto_base = _patch_sync_plumbing(monkeypatch, project)
    svc = _git_service()
    task = _sync_task("feature/backend/abc12345")

    result = await svc.sync_task_branch(task, base_branch="feature/backend/parent")

    assert result == {"status": "rebased", "unique_commits": 1}
    rebase_onto_base.assert_awaited_once()


# ---------------------------------------------------------------------------
# pull() safety-gate tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_raises_validation_error_on_dirty_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pull() raises ValidationError(DIRTY_WORKSPACE) when the tree is dirty.

    The pre-flight ``git status --porcelain`` returns modified files, so pull
    must reject immediately before any network call.
    """
    monkeypatch.setattr(
        GitService,
        "_run_git",
        AsyncMock(return_value=_result(stdout=" M dirty.py\n")),
    )
    svc = _git_service()
    with pytest.raises(ValidationError, match="DIRTY_WORKSPACE"):
        await svc.pull(_WORKSPACE)


@pytest.mark.asyncio
async def test_pull_raises_validation_error_on_diverged_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pull() raises ValidationError(DIVERGED_BRANCH) when --ff-only fails.

    The pre-flight status is clean, but ``git pull --ff-only`` exits non-zero
    with a "not possible to fast-forward" message because the branch has
    diverged from origin.
    """
    monkeypatch.setattr(
        GitService,
        "_run_git",
        AsyncMock(
            side_effect=[
                _result(stdout=""),  # status --porcelain → clean
                _result(  # pull --ff-only → diverged
                    returncode=1,
                    stderr="fatal: Not possible to fast-forward, aborting.",
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        GitService, "_token_for_workspace", AsyncMock(return_value=None)
    )
    svc = _git_service()
    with pytest.raises(ValidationError, match="DIVERGED_BRANCH"):
        await svc.pull(_WORKSPACE)


@pytest.mark.asyncio
async def test_pull_success_returns_post_pull_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pull() returns the post-pull status on a clean, fast-forwardable branch.

    The pre-flight ``git status --porcelain`` is clean and ``git pull --ff-only``
    succeeds, so pull() returns the post-pull ``get_status`` tuple.
    """
    _post_pull: tuple[str, bool, list[str], list[str], list[str], int, int] = (
        "feature/backend/task",
        False,
        [],
        [],
        [],
        0,
        0,
    )
    monkeypatch.setattr(
        GitService,
        "_run_git",
        AsyncMock(side_effect=[_result(stdout=""), _result(returncode=0)]),
    )
    monkeypatch.setattr(
        GitService, "_token_for_workspace", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(GitService, "get_status", AsyncMock(return_value=_post_pull))

    svc = _git_service()
    result = await svc.pull(_WORKSPACE)

    assert result == _post_pull


# ---------------------------------------------------------------------------
# GitRebaseRequest.target_branch field validator tests
# ---------------------------------------------------------------------------


def test_rebase_request_target_branch_dash_prefix_rejected() -> None:
    """GitRebaseRequest rejects target_branch that starts with '-'.

    Branch names beginning with '-' are not valid git ref names and look
    like CLI flags, so the schema validator rejects them with a clear error.
    """
    with pytest.raises(pydantic.ValidationError, match="INVALID_TARGET_BRANCH"):
        GitRebaseRequest(
            project_slug="roboco",
            target_branch="-bad-branch",
        )


def test_rebase_request_target_branch_protected_name_rejected() -> None:
    """GitRebaseRequest rejects target_branch 'main' (a protected branch name)."""
    with pytest.raises(pydantic.ValidationError, match="PROTECTED_BRANCH"):
        GitRebaseRequest(
            project_slug="roboco",
            target_branch="main",
        )


def test_rebase_request_target_branch_master_rejected() -> None:
    """GitRebaseRequest rejects target_branch 'master' (a protected branch name)."""
    with pytest.raises(pydantic.ValidationError, match="PROTECTED_BRANCH"):
        GitRebaseRequest(
            project_slug="roboco",
            target_branch="master",
        )


def test_rebase_request_valid_target_branch_accepted() -> None:
    """GitRebaseRequest accepts a valid, non-protected target_branch."""
    req = GitRebaseRequest(
        project_slug="roboco",
        target_branch="feature/backend/some-task",
    )
    assert req.target_branch == "feature/backend/some-task"


# ---------------------------------------------------------------------------
# Route-level tests: role gate on POST /rebase
# ---------------------------------------------------------------------------


async def _mock_db_generator() -> Any:
    """Async generator yielding a MagicMock as the database session.

    FastAPI's original ``get_db`` is an async generator (uses ``yield``).
    The override must also be a generator (or at least async) so FastAPI
    handles the dependency lifecycle correctly.
    """
    yield MagicMock()


def _build_git_app(agent_context: AgentContext) -> FastAPI:
    """Minimal FastAPI app with the git router and overridden agent context."""
    app = FastAPI()
    app.include_router(git_router, prefix="/git")
    app.dependency_overrides[get_agent_context] = lambda: agent_context
    app.dependency_overrides[get_db] = _mock_db_generator
    return app


@pytest.mark.asyncio
async def test_rebase_endpoint_developer_gets_403() -> None:
    """POST /git/rebase returns HTTP 403 for a DEVELOPER-role agent.

    The role gate fires before any service call, so no git service mock
    is needed.
    """
    agent = AgentContext(agent_id=uuid4(), role=AgentRole.DEVELOPER)
    app = _build_git_app(agent)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/git/rebase",
            json={
                "project_slug": "roboco",
                "target_branch": "feature/backend/some-task",
            },
        )

    assert response.status_code == _HTTP_403
    detail = response.json()["detail"]
    assert "REBASE_ROLE_RESTRICTED" in detail


@pytest.mark.asyncio
async def test_rebase_endpoint_pm_gets_200() -> None:
    """POST /git/rebase returns HTTP 200 for a CELL_PM-role agent.

    The role gate passes; no task_id is supplied so the ownership check
    is skipped; project resolution and the git service are patched.
    """
    agent = AgentContext(agent_id=uuid4(), role=AgentRole.CELL_PM)
    app = _build_git_app(agent)

    # Mock project service → returns a project with slug "roboco"
    mock_project = MagicMock()
    mock_project.slug = "roboco"
    mock_project_svc = MagicMock()
    mock_project_svc.get_by_slug = AsyncMock(return_value=mock_project)

    # Mock git service → workspace + rebase succeed without conflict
    mock_git_svc = MagicMock()
    mock_git_svc.get_workspace = AsyncMock(return_value=Path("/tmp/fake-ws"))
    mock_git_svc.rebase = AsyncMock(return_value=(False, []))

    transport = ASGITransport(app=app)

    with (
        patch(
            "roboco.api.routes.git.get_project_service", return_value=mock_project_svc
        ),
        patch("roboco.api.routes.git.get_git_service", return_value=mock_git_svc),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/git/rebase",
                json={
                    "project_slug": "roboco",
                    "target_branch": "feature/backend/some-task",
                },
            )

    assert response.status_code == _HTTP_200
    body = response.json()
    assert body["project_slug"] == "roboco"
    assert body["conflict"] is False
    assert body["conflicted_files"] == []

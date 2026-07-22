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
from roboco.services.forge import RepoRef
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
            RepoRef("acme", "repo"), "feature/main_pm/abc123", "tok"
        )
    client.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_removes_leaf_branch_with_no_dependents() -> None:
    svc = _service()
    _bind(svc, "_branch_has_open_dependents", AsyncMock(return_value=False))
    client = _fake_client()
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        await svc._delete_remote_branch_best_effort(
            RepoRef("acme", "repo"), "feature/backend/abc--cell--leaf", "tok"
        )
    client.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_skips_default_branch_before_checking_dependents() -> None:
    svc = _service()
    dep = AsyncMock(return_value=False)
    _bind(svc, "_branch_has_open_dependents", dep)
    client = _fake_client()
    with patch("roboco.services.git.httpx.AsyncClient", return_value=client):
        await svc._delete_remote_branch_best_effort(
            RepoRef("acme", "repo"), "master", "tok"
        )
    client.delete.assert_not_awaited()
    dep.assert_not_awaited()


# --- projects.protected_branches UNION (2026-07-22 follow-up) -------------
# `_delete_remote_branch_best_effort` unions its hardcoded skip tuple with
# the project's own declared `protected_branches` when a project_slug is
# given. The union can only ADD protection: a missing project_slug, an
# unresolvable project, or an emptied field must reproduce the exact
# hardcoded-only behavior above.


def _project_service_returning(project: MagicMock) -> MagicMock:
    svc = MagicMock()
    svc.get_by_slug = AsyncMock(return_value=project)
    return svc


@pytest.mark.asyncio
async def test_delete_skips_project_declared_protected_branch() -> None:
    """A custom protected branch (not in the hardcoded set) is refused when
    the project declares it — the open-dependents probe is never reached,
    mirroring the hardcoded-name short-circuit above."""
    svc = _service()
    dep = AsyncMock(return_value=False)
    _bind(svc, "_branch_has_open_dependents", dep)
    client = _fake_client()
    project = MagicMock(protected_branches=["release"])
    with (
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
        patch(
            "roboco.services.git.get_project_service",
            return_value=_project_service_returning(project),
        ),
    ):
        await svc._delete_remote_branch_best_effort(
            RepoRef("acme", "repo"), "release", "tok", "acme-repo"
        )
    client.delete.assert_not_awaited()
    dep.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_allows_branch_not_in_projects_protected_list() -> None:
    """A branch that isn't hardcoded AND isn't in the project's declared
    list is deleted normally — the union only blocks what's actually
    listed, it doesn't become deny-by-default."""
    svc = _service()
    _bind(svc, "_branch_has_open_dependents", AsyncMock(return_value=False))
    client = _fake_client()
    project = MagicMock(protected_branches=["release"])
    with (
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
        patch(
            "roboco.services.git.get_project_service",
            return_value=_project_service_returning(project),
        ),
    ):
        await svc._delete_remote_branch_best_effort(
            RepoRef("acme", "repo"),
            "feature/backend/abc--cell--leaf",
            "tok",
            "acme-repo",
        )
    client.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_empty_protected_branches_matches_hardcoded_only_behavior() -> (
    None
):
    """An empty (or null) protected_branches field degrades to exactly the
    prior hardcoded-only behavior — clearing the list never loosens
    anything, but it also never invents new protection."""
    svc = _service()
    _bind(svc, "_branch_has_open_dependents", AsyncMock(return_value=False))
    client = _fake_client()
    project = MagicMock(protected_branches=[])
    with (
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
        patch(
            "roboco.services.git.get_project_service",
            return_value=_project_service_returning(project),
        ),
    ):
        await svc._delete_remote_branch_best_effort(
            RepoRef("acme", "repo"),
            "feature/backend/abc--cell--leaf",
            "tok",
            "acme-repo",
        )
    client.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_no_project_slug_matches_hardcoded_only_behavior() -> None:
    """Omitting project_slug entirely (legacy call shape) never touches the
    project service and behaves byte-for-byte like before this change."""
    svc = _service()
    _bind(svc, "_branch_has_open_dependents", AsyncMock(return_value=False))
    client = _fake_client()
    with (
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
        patch("roboco.services.git.get_project_service") as get_project_service,
    ):
        await svc._delete_remote_branch_best_effort(
            RepoRef("acme", "repo"), "feature/backend/abc--cell--leaf", "tok"
        )
    client.delete.assert_awaited_once()
    get_project_service.assert_not_called()


@pytest.mark.asyncio
async def test_delete_matches_stripped_branch_case_sensitively() -> None:
    """Stored entries are stripped of whitespace defensively, but matching
    stays case-sensitive (git branch names are case-sensitive): a
    differently-cased request is NOT protected by a stored ' Release '."""
    svc = _service()
    dep = AsyncMock(return_value=False)
    _bind(svc, "_branch_has_open_dependents", dep)
    client = _fake_client()
    project = MagicMock(protected_branches=[" Release "])
    with (
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
        patch(
            "roboco.services.git.get_project_service",
            return_value=_project_service_returning(project),
        ),
    ):
        # Exact match after stripping -> refused.
        await svc._delete_remote_branch_best_effort(
            RepoRef("acme", "repo"), "Release", "tok", "acme-repo"
        )
    client.delete.assert_not_awaited()
    dep.assert_not_awaited()

    client2 = _fake_client()
    with (
        patch("roboco.services.git.httpx.AsyncClient", return_value=client2),
        patch(
            "roboco.services.git.get_project_service",
            return_value=_project_service_returning(project),
        ),
    ):
        # Different case -> not the same git ref -> allowed.
        await svc._delete_remote_branch_best_effort(
            RepoRef("acme", "repo"), "release", "tok", "acme-repo"
        )
    client2.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_union_never_collapses_hardcoded_floor_to_project_list_only() -> (
    None
):
    """A project declaring its OWN protected_branches (e.g. ["release"]) must
    NOT replace the hardcoded main/master/develop skip — the union is
    additive, never a substitution. master and main stay refused regardless
    of what the project's list contains."""
    project = MagicMock(protected_branches=["release"])

    svc = _service()
    dep = AsyncMock(return_value=False)
    _bind(svc, "_branch_has_open_dependents", dep)
    client = _fake_client()
    with (
        patch("roboco.services.git.httpx.AsyncClient", return_value=client),
        patch(
            "roboco.services.git.get_project_service",
            return_value=_project_service_returning(project),
        ),
    ):
        await svc._delete_remote_branch_best_effort(
            RepoRef("acme", "repo"), "master", "tok", "acme-repo"
        )
    client.delete.assert_not_awaited()
    dep.assert_not_awaited()

    client2 = _fake_client()
    with (
        patch("roboco.services.git.httpx.AsyncClient", return_value=client2),
        patch(
            "roboco.services.git.get_project_service",
            return_value=_project_service_returning(project),
        ),
    ):
        await svc._delete_remote_branch_best_effort(
            RepoRef("acme", "repo"), "main", "tok", "acme-repo"
        )
    client2.delete.assert_not_awaited()


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
            RepoRef("acme", "repo"), "feature/main_pm/abc123", "tok"
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
            RepoRef("acme", "repo"), "feature/x--leaf", "tok"
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
            RepoRef("acme", "repo"), "feature/main_pm/abc123", "tok"
        )
    assert out is True

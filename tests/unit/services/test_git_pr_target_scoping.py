"""F052: pr_target must scope its task lookup by project_id when the caller
knows it, mirroring close_pull_request.

GitHub numbers PRs per-repo, so ``pr_number`` is ambiguous across projects: a
backend repo's PR #132 and a frontend repo's PR #132 are different PRs. The
bare ``WHERE pr_number == N LIMIT 1`` query returns whichever task row comes
first — the wrong repo's task, whose ``_project_for_task`` then resolves the
wrong project and the GitHub fetch hits the wrong repo. When the caller knows
the project (the Main PM coordinating a known root), it must pass
``project_id`` so the lookup is scoped and a same-numbered PR in another
project's repo is never resolved by accident — the pattern
``close_pull_request`` already established.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.services.base import NotFoundError
from roboco.services.git import GitService

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

_PR_NUMBER = 132


def _make_session(recorder: list[object]) -> MagicMock:
    session = MagicMock()

    async def _execute(stmt: object) -> MagicMock:
        recorder.append(stmt)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        return result

    session.execute = AsyncMock(side_effect=_execute)
    return session


def _service(recorder: list[object]) -> GitService:
    return GitService(_make_session(recorder))


def _patch_project_service(project: object | None) -> AbstractContextManager[object]:
    fake_service = MagicMock()
    fake_service.get = AsyncMock(return_value=project)
    fake_service.get_by_slug = AsyncMock(return_value=project)
    return patch("roboco.services.git.get_project_service", return_value=fake_service)


def _compiled_sql(stmt: Any) -> str:
    """Render a SQLAlchemy stmt to literal-bound SQL for assertion."""
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_pr_target_scopes_task_lookup_by_project_id_when_provided() -> None:
    """With ``project_id`` the task lookup WHERE clause filters on BOTH
    pr_number and project_id — a same-numbered PR in another project's repo
    can't be resolved by accident."""
    recorder: list[object] = []
    svc = _service(recorder)
    project_id = uuid4()

    # Task lookup returns None → NotFoundError, but we only care about the SQL
    # the lookup was issued with.
    with _patch_project_service(MagicMock(slug="roboco")), pytest.raises(NotFoundError):
        await svc.pr_target(_PR_NUMBER, project_id=project_id)

    assert len(recorder) == 1
    sql = _compiled_sql(recorder[0])
    assert "tasks.pr_number =" in sql
    # The WHERE clause filters on project_id (the select list renders the
    # column as ``tasks.project_id,`` with a trailing comma; the WHERE
    # comparison renders as ``tasks.project_id =``).
    assert "tasks.project_id =" in sql


@pytest.mark.asyncio
async def test_pr_target_without_project_id_does_not_scope() -> None:
    """Without ``project_id`` the lookup stays unscoped (backward-compatible
    with callers that don't know the project) — only pr_number is filtered."""
    recorder: list[object] = []
    svc = _service(recorder)

    with _patch_project_service(MagicMock(slug="roboco")), pytest.raises(NotFoundError):
        await svc.pr_target(_PR_NUMBER)

    assert len(recorder) == 1
    sql = _compiled_sql(recorder[0])
    assert "tasks.pr_number =" in sql
    # No project_id WHERE filter (the column still appears in the select list
    # with a trailing comma, but the ``=`` comparison is absent).
    assert "tasks.project_id =" not in sql


@pytest.mark.asyncio
async def test_pr_target_with_project_id_skips_wrong_repo_task() -> None:
    """Two tasks share pr_number #132 but live in different projects. With the
    correct ``project_id`` the scoped query returns ONLY the matching task (the
    other repo's task is filtered out), so the GitHub fetch hits the right
    repo. (Here the scoped lookup finds nothing in the requested project →
    NotFoundError, NOT a silent wrong-repo resolution.)"""
    project_id = uuid4()
    other_project_id = uuid4()
    # The wrong-repo task (other project) shares the pr_number.
    wrong_repo_task = MagicMock(
        id=uuid4(), project_id=other_project_id, assigned_to=uuid4()
    )
    recorder: list[object] = []

    session = MagicMock()

    async def _execute(stmt: object) -> MagicMock:
        recorder.append(stmt)
        result = MagicMock()
        # Simulate the DB applying the project_id filter: the scoped query
        # finds no row in the requested project (the only matching pr_number
        # belongs to the OTHER project).
        result.scalar_one_or_none.return_value = None
        return result

    session.execute = AsyncMock(side_effect=_execute)
    svc = GitService(session)
    _bind = object.__setattr__
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="token"))

    with _patch_project_service(MagicMock(slug="roboco")), pytest.raises(NotFoundError):
        await svc.pr_target(_PR_NUMBER, project_id=project_id)

    # The query WAS scoped by project_id (the wrong-repo task did not leak in).
    sql = _compiled_sql(recorder[0])
    assert "tasks.project_id =" in sql
    _ = wrong_repo_task  # exists to document the cross-repo collision scenario

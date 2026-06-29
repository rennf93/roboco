"""close_pull_request must scope its task lookup by project_id — always.

GitHub numbers PRs per-repo, so ``pr_number`` is ambiguous across projects: a
backend repo's PR #159 and a frontend repo's PR #159 are different PRs. The
bare ``WHERE pr_number == N LIMIT 1`` query returns whichever task row comes
first — the wrong repo's task, whose ``_project_for_task`` resolves the wrong
project and the GitHub close hits (and closes!) the wrong repo's PR.

``project_id`` is therefore MANDATORY: the caller can never close a PR without
scoping it to a project, so a same-numbered PR in another project's repo is
unreachable by accident — the pattern ``pr_merge`` / ``pr_target`` already
established.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.services.base import NotFoundError
from roboco.services.git import GitService

_PR_NUMBER = 159


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


def _patch_project_service(project: object | None) -> Any:
    fake_service = MagicMock()
    fake_service.get = AsyncMock(return_value=project)
    fake_service.get_by_slug = AsyncMock(return_value=project)
    return patch("roboco.services.git.get_project_service", return_value=fake_service)


def _compiled_sql(stmt: Any) -> str:
    """Render a SQLAlchemy stmt to literal-bound SQL for assertion."""
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_close_pull_request_scopes_task_lookup_by_project_id() -> None:
    """The task lookup WHERE clause filters on BOTH pr_number and project_id
    — a same-numbered PR in another project's repo can't be resolved by
    accident."""
    recorder: list[object] = []
    svc = _service(recorder)
    project_id = uuid4()

    # Task lookup returns None → NotFoundError, but we only care about the SQL
    # the lookup was issued with.
    with _patch_project_service(MagicMock(slug="roboco")), pytest.raises(NotFoundError):
        await svc.close_pull_request(_PR_NUMBER, project_id=project_id)

    assert len(recorder) == 1
    sql = _compiled_sql(recorder[0])
    assert "tasks.pr_number =" in sql
    # The WHERE clause filters on project_id (the select list renders the
    # column as ``tasks.project_id,`` with a trailing comma; the WHERE
    # comparison renders as ``tasks.project_id =``).
    assert "tasks.project_id =" in sql


@pytest.mark.asyncio
async def test_close_pull_request_requires_project_id() -> None:
    """``project_id`` is mandatory — a caller can NEVER close a PR without
    scoping it to a project. Omitting it is a programming error (TypeError at
    call time), not a silent unscoped lookup that could close another
    project's same-numbered PR (the cross-repo collision)."""
    recorder: list[object] = []
    svc = _service(recorder)

    # Bind to an Any-typed local so mypy doesn't flag the missing project_id;
    # the call still reaches the runtime, where it raises TypeError as asserted.
    closer: Any = svc.close_pull_request
    with pytest.raises(TypeError):
        await closer(_PR_NUMBER, comment="superseded")

    # The unscoped lookup was never issued — no SQL reached the session.
    assert recorder == []

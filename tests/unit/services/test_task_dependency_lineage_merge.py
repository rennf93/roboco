"""``TaskService._apply_dependency_lineage`` — orchestration of the
cross-subtree/cross-cell dependency-lineage gap fix at the branch-cut seam.

The dependency gate (``_claim_blocked_by_dependencies``) enforces TIMING —
a task can't claim until every ``dependency_ids`` entry is terminal — but
never CONTENT: a dependency's merged work can sit on a branch this task's
freshly cut branch never descends from (a cross-subtree/cross-cell edge, or
a batch cross-root edge). ``_apply_dependency_lineage`` runs right after
branch creation, resolves each dependency's real merge target via
``merge_chain.resolve_parent_branch``, and delegates the ancestor check +
merge to ``GitService.merge_dependency_lineage`` (covered separately in
``test_git_dependency_lineage_merge.py``). This is best-effort by
construction: nothing here may ever raise back into the claim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.services.task import TaskService, _LineageCutContext

_MERGE_CHAIN_RESOLVE = "roboco.services.gateway.merge_chain.resolve_parent_branch"
_WORKSPACE = Path("/tmp/ws")
_BRANCH = "feature/frontend/root--fe-cell"


def _service() -> TaskService:
    svc = TaskService.__new__(TaskService)
    svc.log = MagicMock()
    session = MagicMock()
    session.flush = AsyncMock()
    svc.session = session
    return svc


def _task(**over: Any) -> MagicMock:
    task = MagicMock(
        id=over.pop("id", uuid4()),
        project_id=over.pop("project_id", uuid4()),
        dependency_ids=over.pop("dependency_ids", []),
        orchestration_markers=over.pop("orchestration_markers", None),
        branch_name=over.pop("branch_name", _BRANCH),
    )
    for key, value in over.items():
        setattr(task, key, value)
    return task


def _ctx(**over: Any) -> _LineageCutContext:
    git_service = over.pop("git_service", None) or MagicMock()
    project = over.pop("project", None) or MagicMock(id=uuid4(), slug="roboco-api")
    return _LineageCutContext(
        git_service=git_service,
        workspace=over.pop("workspace", _WORKSPACE),
        project=project,
    )


@pytest.mark.asyncio
async def test_no_dependencies_does_no_work() -> None:
    """Zero dependency_ids: no DB lookups, no git calls, no flush."""
    svc = _service()
    any_svc: Any = svc
    any_svc.get = AsyncMock()
    task = _task(dependency_ids=[])
    ctx = _ctx()

    await svc._apply_dependency_lineage(task, ctx)

    any_svc.get.assert_not_awaited()
    ctx.git_service.merge_dependency_lineage.assert_not_called()
    any_svc.session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_project_dependency_resolves_source_and_calls_merge() -> None:
    """A same-repo dependency: resolve its real merge target (the branch its
    PR merged into) and hand it to GitService for the ancestor check/merge."""
    svc = _service()
    any_svc: Any = svc
    project = MagicMock(id=uuid4(), slug="roboco-api")
    dep_id = uuid4()
    dep_task = _task(id=dep_id, project_id=project.id)
    task = _task(project_id=project.id, dependency_ids=[dep_id])
    any_svc.get = AsyncMock(return_value=dep_task)

    git_service = MagicMock()
    any_git_service: Any = git_service
    any_git_service.merge_dependency_lineage = AsyncMock(
        return_value={"status": "merged"}
    )
    ctx = _ctx(git_service=git_service, project=project)

    with patch(
        _MERGE_CHAIN_RESOLVE,
        AsyncMock(return_value="feature/ux_ui/root--ux-cell"),
    ):
        await svc._apply_dependency_lineage(task, ctx)

    any_git_service.merge_dependency_lineage.assert_awaited_once_with(
        _WORKSPACE,
        task.id,
        _BRANCH,
        "feature/ux_ui/root--ux-cell",
        project_slug="roboco-api",
    )


@pytest.mark.asyncio
async def test_cross_project_dependency_is_skipped() -> None:
    """A dependency in a DIFFERENT repo has no shared git history — skip it
    rather than attempt a meaningless cross-repo merge."""
    svc = _service()
    any_svc: Any = svc
    project = MagicMock(id=uuid4(), slug="roboco-api")
    dep_id = uuid4()
    dep_task = _task(id=dep_id, project_id=uuid4())  # different project
    task = _task(project_id=project.id, dependency_ids=[dep_id])
    any_svc.get = AsyncMock(return_value=dep_task)
    ctx = _ctx(project=project)

    await svc._apply_dependency_lineage(task, ctx)

    ctx.git_service.merge_dependency_lineage.assert_not_called()


@pytest.mark.asyncio
async def test_missing_dependency_task_is_skipped() -> None:
    """A dependency id that no longer resolves to a task: skip, don't crash."""
    svc = _service()
    any_svc: Any = svc
    project = MagicMock(id=uuid4(), slug="roboco-api")
    task = _task(project_id=project.id, dependency_ids=[uuid4()])
    any_svc.get = AsyncMock(return_value=None)
    ctx = _ctx(project=project)

    await svc._apply_dependency_lineage(task, ctx)

    ctx.git_service.merge_dependency_lineage.assert_not_called()


@pytest.mark.asyncio
async def test_conflict_status_notes_transition_and_warns_but_does_not_raise() -> None:
    """A real conflict never fails the claim: it logs + leaves a task note
    naming the dependency and its branch so a human follows up."""
    svc = _service()
    any_svc: Any = svc
    project = MagicMock(id=uuid4(), slug="roboco-api")
    dep_id = uuid4()
    dep_task = _task(id=dep_id, project_id=project.id)
    task = _task(project_id=project.id, dependency_ids=[dep_id])
    any_svc.get = AsyncMock(return_value=dep_task)

    git_service = MagicMock()
    any_git_service: Any = git_service
    any_git_service.merge_dependency_lineage = AsyncMock(
        return_value={"status": "conflict", "files": ["src/a.py"]}
    )
    ctx = _ctx(git_service=git_service, project=project)

    with patch(
        _MERGE_CHAIN_RESOLVE,
        AsyncMock(return_value="feature/ux_ui/root--ux-cell"),
    ):
        await svc._apply_dependency_lineage(task, ctx)

    note = markers.get_transition_note(task, "dependency_lineage_conflict")
    assert note is not None
    assert str(dep_id) in note
    assert "src/a.py" in note
    assert "feature/ux_ui/root--ux-cell" in note
    svc.log.warning.assert_called()


@pytest.mark.asyncio
async def test_second_conflict_appends_rather_than_overwrites() -> None:
    """Two conflicting dependencies on one branch: both surface, not just
    the last one — set_transition_note's per-event dict would otherwise
    silently drop the first."""
    svc = _service()
    any_svc: Any = svc
    project = MagicMock(id=uuid4(), slug="roboco-api")
    dep_a, dep_b = uuid4(), uuid4()
    dep_task_a = _task(id=dep_a, project_id=project.id)
    dep_task_b = _task(id=dep_b, project_id=project.id)
    task = _task(project_id=project.id, dependency_ids=[dep_a, dep_b])
    any_svc.get = AsyncMock(side_effect=[dep_task_a, dep_task_b])

    git_service = MagicMock()
    any_git_service: Any = git_service
    any_git_service.merge_dependency_lineage = AsyncMock(
        side_effect=[
            {"status": "conflict", "files": ["a.py"]},
            {"status": "conflict", "files": ["b.py"]},
        ]
    )
    ctx = _ctx(git_service=git_service, project=project)

    with patch(
        _MERGE_CHAIN_RESOLVE,
        AsyncMock(side_effect=["feature/x/one", "feature/x/two"]),
    ):
        await svc._apply_dependency_lineage(task, ctx)

    note = markers.get_transition_note(task, "dependency_lineage_conflict")
    assert note is not None
    assert str(dep_a) in note
    assert str(dep_b) in note


@pytest.mark.asyncio
async def test_merge_error_is_swallowed_never_raised() -> None:
    """This is a claim-time content assist, never a gate: any unexpected
    failure (network, resolver bug) is logged and swallowed."""
    svc = _service()
    any_svc: Any = svc
    project = MagicMock(id=uuid4(), slug="roboco-api")
    dep_id = uuid4()
    dep_task = _task(id=dep_id, project_id=project.id)
    task = _task(project_id=project.id, dependency_ids=[dep_id])
    any_svc.get = AsyncMock(return_value=dep_task)

    git_service = MagicMock()
    any_git_service: Any = git_service
    any_git_service.merge_dependency_lineage = AsyncMock(
        side_effect=RuntimeError("boom")
    )
    ctx = _ctx(git_service=git_service, project=project)

    with patch(_MERGE_CHAIN_RESOLVE, AsyncMock(return_value="feature/x/one")):
        await svc._apply_dependency_lineage(task, ctx)

    svc.log.warning.assert_called()


@pytest.mark.asyncio
async def test_create_branch_in_project_wires_apply_dependency_lineage() -> None:
    """Wiring check: after a successful branch cut, _create_branch_in_project
    calls the lineage step with the CONCRETE project just resolved — correct
    both for a plain task and inside the coordination-root per-project loop
    (_ensure_coordination_root_branches calls this once per spanned repo)."""
    svc = _service()
    task = _task(branch_name=None, dependency_ids=[uuid4()])
    project = MagicMock(id=task.project_id, slug="roboco-api")

    object.__setattr__(svc, "_resolve_parent_branch", AsyncMock(return_value=None))
    object.__setattr__(svc, "_resolve_team_dir", MagicMock(return_value="backend"))

    git_service = MagicMock()
    any_git_service: Any = git_service
    any_git_service.get_workspace = AsyncMock(return_value=_WORKSPACE)
    any_git_service.create_branch = AsyncMock(return_value=("feature/x", "master"))

    applied: list[tuple[Any, _LineageCutContext]] = []

    async def _fake_apply(t: Any, ctx: _LineageCutContext) -> None:
        applied.append((t, ctx))

    object.__setattr__(svc, "_apply_dependency_lineage", _fake_apply)

    with patch(
        "roboco.services.git.get_git_service", MagicMock(return_value=git_service)
    ):
        out = await svc._create_branch_in_project(task, uuid4(), project)

    assert out == "feature/x"
    assert len(applied) == 1
    got_task, got_ctx = applied[0]
    assert got_task is task
    assert got_ctx.git_service is git_service
    assert got_ctx.workspace == _WORKSPACE
    assert got_ctx.project is project

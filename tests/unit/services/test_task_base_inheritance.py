"""Upstream base inheritance on re-claim.

A re-claim reuses a branch cut at an earlier claim; upstream work merged
since (a sibling UX/UI cell landing on the root, master advancing under a
root) never reached it, so BE/FE branches diverged from design work they
were meant to build on. ``_finalize_claim`` now merges the advanced base
into the pre-existing branch on WORK claims (developer / cell_pm / main_pm)
via ``_inherit_upstream_base`` — QA/doc/gate claims never move the branch,
and a fresh cut already branches from the live remote base.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.task import TaskService


def _service() -> TaskService:
    svc = TaskService.__new__(TaskService)
    svc.log = MagicMock()
    session = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    svc.session = session
    return svc


def _claim_task(
    branch_name: str | None,
    project_id: Any = None,
    status: TaskStatus = TaskStatus.PENDING,
) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        project_id=project_id if project_id is not None else uuid4(),
        branch_name=branch_name,
        status=status,
        assigned_to=None,
        claimed_by=None,
        claimed_at=None,
        last_heartbeat_at=None,
        active_claimant_id=None,
        orchestration_markers={},
    )


def _wire_finalize(svc: TaskService) -> AsyncMock:
    """Stub every _finalize_claim collaborator; return the inherit mock."""
    object.__setattr__(svc, "_set_original_developer_context", MagicMock())
    object.__setattr__(svc, "_validate_and_set_status", MagicMock())
    object.__setattr__(svc, "_emit_status_transition_audit", MagicMock())
    object.__setattr__(svc, "_ensure_branch_for_task", AsyncMock(return_value="b"))
    object.__setattr__(svc, "_create_work_session_if_needed", AsyncMock())
    object.__setattr__(svc, "_inject_proactive_context", AsyncMock())
    object.__setattr__(svc, "_CLAIMABLE_STATUSES", {TaskStatus.PENDING})
    inherit = AsyncMock()
    object.__setattr__(svc, "_inherit_upstream_base", inherit)
    return inherit


def _agent(role: str) -> MagicMock:
    agent = MagicMock()
    agent.role.value = role
    return agent


# ---------------------------------------------------------------------------
# _finalize_claim gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dev_reclaim_with_existing_branch_inherits() -> None:
    svc = _service()
    inherit = _wire_finalize(svc)
    task = _claim_task("feature/backend/AAA--BBB")

    await svc._finalize_claim(task, _agent("developer"), uuid4())

    inherit.assert_awaited_once()


@pytest.mark.asyncio
async def test_pm_reclaim_with_existing_branch_inherits() -> None:
    svc = _service()
    inherit = _wire_finalize(svc)
    task = _claim_task("feature/main_pm/AAA")

    await svc._finalize_claim(task, _agent("cell_pm"), uuid4())

    inherit.assert_awaited_once()


@pytest.mark.asyncio
async def test_pm_review_queue_reclaim_never_inherits() -> None:
    """A PM's i_will_plan re-claim of its own AWAITING_PM_REVIEW task must
    not move a branch that already passed QA + the PR gate — a silent base
    merge there would put unreviewed content under the merge decision."""
    svc = _service()
    inherit = _wire_finalize(svc)
    task = _claim_task("feature/main_pm/AAA", status=TaskStatus.AWAITING_PM_REVIEW)

    await svc._finalize_claim(task, _agent("cell_pm"), uuid4())

    inherit.assert_not_awaited()


@pytest.mark.asyncio
async def test_needs_revision_reclaim_inherits() -> None:
    """A bounced task re-claimed by its dev is the flagship inherit case."""
    svc = _service()
    inherit = _wire_finalize(svc)
    task = _claim_task("feature/backend/AAA--BBB", status=TaskStatus.NEEDS_REVISION)

    await svc._finalize_claim(task, _agent("developer"), uuid4())

    inherit.assert_awaited_once()


@pytest.mark.asyncio
async def test_qa_claim_never_moves_the_branch() -> None:
    svc = _service()
    inherit = _wire_finalize(svc)
    task = _claim_task("feature/backend/AAA--BBB")

    await svc._finalize_claim(task, _agent("qa"), uuid4())

    inherit.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_branch_skips_inheritance() -> None:
    """No pre-claim branch → the fresh cut already builds on the live base."""
    svc = _service()
    inherit = _wire_finalize(svc)
    task = _claim_task(None)

    await svc._finalize_claim(task, _agent("developer"), uuid4())

    inherit.assert_not_awaited()


@pytest.mark.asyncio
async def test_branchless_coordination_skips_inheritance() -> None:
    svc = _service()
    inherit = _wire_finalize(svc)
    task = _claim_task("feature/main_pm/AAA")
    task.project_id = None

    await svc._finalize_claim(task, _agent("main_pm"), uuid4())

    inherit.assert_not_awaited()


# ---------------------------------------------------------------------------
# _inherit_upstream_base behavior
# ---------------------------------------------------------------------------


def _patched_deps(
    svc: TaskService,
    merge_status: dict[str, Any] | Exception,
    parent_branch: str = "feature/main_pm/AAA",
) -> tuple[Any, Any, AsyncMock]:
    project = MagicMock(slug="roboco-api")
    proj_svc = MagicMock()
    proj_svc.get = AsyncMock(return_value=project)
    git_svc = MagicMock()
    git_svc.get_workspace = AsyncMock(return_value=MagicMock())
    merge = AsyncMock(
        side_effect=merge_status
        if isinstance(merge_status, Exception)
        else [merge_status]
    )
    git_svc.merge_dependency_lineage = merge
    object.__setattr__(
        svc, "_resolve_parent_branch", AsyncMock(return_value=parent_branch)
    )
    return proj_svc, git_svc, merge


@pytest.mark.asyncio
async def test_inherit_merges_parent_branch() -> None:
    svc = _service()
    task = _claim_task("feature/backend/AAA--BBB")
    proj_svc, git_svc, merge = _patched_deps(svc, {"status": "merged"})

    with (
        patch(
            "roboco.services.project.get_project_service",
            MagicMock(return_value=proj_svc),
        ),
        patch("roboco.services.git.get_git_service", MagicMock(return_value=git_svc)),
    ):
        await svc._inherit_upstream_base(task, uuid4())

    merge.assert_awaited_once()
    args = merge.await_args
    assert args is not None
    assert args.args[2] == "feature/backend/AAA--BBB"
    assert args.args[3] == "feature/main_pm/AAA"
    assert task.orchestration_markers == {}, "clean merge leaves no note"


@pytest.mark.asyncio
async def test_inherit_conflict_notes_the_task() -> None:
    svc = _service()
    task = _claim_task("feature/backend/AAA--BBB")
    proj_svc, git_svc, _ = _patched_deps(
        svc, {"status": "conflict", "files": ["a.py", "b.py"]}
    )

    with (
        patch(
            "roboco.services.project.get_project_service",
            MagicMock(return_value=proj_svc),
        ),
        patch("roboco.services.git.get_git_service", MagicMock(return_value=git_svc)),
    ):
        await svc._inherit_upstream_base(task, uuid4())

    note = markers.get_transition_note(task, "base_inheritance_conflict")
    assert note is not None
    assert "a.py, b.py" in note
    assert "sync_branch" in note


@pytest.mark.asyncio
async def test_inherit_skips_when_base_equals_branch() -> None:
    svc = _service()
    task = _claim_task("feature/main_pm/AAA")
    proj_svc, git_svc, merge = _patched_deps(
        svc, {"status": "merged"}, parent_branch="feature/main_pm/AAA"
    )

    with (
        patch(
            "roboco.services.project.get_project_service",
            MagicMock(return_value=proj_svc),
        ),
        patch("roboco.services.git.get_git_service", MagicMock(return_value=git_svc)),
    ):
        await svc._inherit_upstream_base(task, uuid4())

    merge.assert_not_awaited()


@pytest.mark.asyncio
async def test_inherit_never_fails_the_claim() -> None:
    svc = _service()
    task = _claim_task("feature/backend/AAA--BBB")
    proj_svc, git_svc, _ = _patched_deps(svc, RuntimeError("network down"))

    with (
        patch(
            "roboco.services.project.get_project_service",
            MagicMock(return_value=proj_svc),
        ),
        patch("roboco.services.git.get_git_service", MagicMock(return_value=git_svc)),
    ):
        await svc._inherit_upstream_base(task, uuid4())  # must not raise

    svc.log.warning.assert_called()

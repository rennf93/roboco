"""i_am_done behind-base submit gate (multi-level sequencing Phase B2).

A sibling's PR merging into the parent branch while a dev worked leaves the
dev's branch behind its base — the assembled PR then can't merge cleanly and
the sibling's changes go missing (the 2026-06-27 out-of-order dev-task break).
The behind-base gate refuses ``i_am_done`` in that state and steers the dev to
``sync_branch`` (the gate-level rebase). Fail-open on git / base-resolution
error so a flaky fetch can't strand a task at the submit gate; the merge layer
has its own behind checks. Skipped for branchless roots and protected bases.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer._impl import _IAmDoneContext


def _make_deps(**overrides: object) -> ChoreographerDeps:
    base: dict[str, object] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    repo = base["evidence_repo"]
    assert isinstance(repo, AsyncMock)
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
    ):
        getattr(repo, method).return_value = []
    return ChoreographerDeps(**base)


def _ctx(
    task: object, *, agent_id: UUID | None = None, task_id: UUID | None = None
) -> _IAmDoneContext:
    return _IAmDoneContext(
        agent_id=agent_id or uuid4(),
        task_id=task_id or uuid4(),
        task=task,
        role_str="developer",
        briefing={},
        notes="",
    )


_BRANCH = "feature/backend/abc12345"
_BASE = "feature/backend/parent12345"


class _Task:
    def __init__(self, branch_name: str | None = _BRANCH) -> None:
        self.branch_name = branch_name


@pytest.mark.asyncio
async def test_behind_base_gate_refuses_and_steers_to_sync_branch() -> None:
    t = _Task()
    task_svc = AsyncMock()
    git_svc = AsyncMock()
    git_svc.is_behind_base.return_value = (3, 2)  # 3 behind, 2 ahead
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)
    ctx = _ctx(t)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value=_BASE),
    ):
        env = await c._behind_base_gate(ctx)

    git_svc.is_behind_base.assert_awaited_once()
    assert env is not None
    assert env.error == "invalid_state"
    assert "sync_branch" in (env.remediate or "")
    assert "behind" in (env.message or "")


@pytest.mark.asyncio
async def test_behind_base_gate_passes_when_up_to_date() -> None:
    t = _Task()
    task_svc = AsyncMock()
    git_svc = AsyncMock()
    git_svc.is_behind_base.return_value = (0, 5)  # not behind
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)
    ctx = _ctx(t)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value=_BASE),
    ):
        env = await c._behind_base_gate(ctx)

    assert env is None


@pytest.mark.asyncio
async def test_behind_base_gate_skips_branchless_task() -> None:
    # No branch_name → branchless coordination root; nothing to sync.
    t = _Task(branch_name=None)
    git_svc = AsyncMock()
    deps = _make_deps(git=git_svc)
    c = Choreographer(deps)
    ctx = _ctx(t)

    env = await c._behind_base_gate(ctx)

    assert env is None
    git_svc.is_behind_base.assert_not_awaited()


@pytest.mark.asyncio
async def test_behind_base_gate_skips_protected_base() -> None:
    # A base that resolved to master must never be rebased into — skip (the
    # merge layer guards master); never block the submit on it.
    t = _Task()
    git_svc = AsyncMock()
    deps = _make_deps(git=git_svc)
    c = Choreographer(deps)
    ctx = _ctx(t)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value="master"),
    ):
        env = await c._behind_base_gate(ctx)

    assert env is None
    git_svc.is_behind_base.assert_not_awaited()


@pytest.mark.asyncio
async def test_behind_base_gate_fail_opens_on_base_resolution_error() -> None:
    t = _Task()
    git_svc = AsyncMock()
    deps = _make_deps(git=git_svc)
    c = Choreographer(deps)
    ctx = _ctx(t)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(side_effect=RuntimeError("db unavailable")),
    ):
        env = await c._behind_base_gate(ctx)

    assert env is None
    git_svc.is_behind_base.assert_not_awaited()


@pytest.mark.asyncio
async def test_behind_base_gate_fail_opens_on_git_error() -> None:
    t = _Task()
    git_svc = AsyncMock()
    git_svc.is_behind_base.side_effect = RuntimeError("fetch timeout")
    deps = _make_deps(git=git_svc)
    c = Choreographer(deps)
    ctx = _ctx(t)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value=_BASE),
    ):
        env = await c._behind_base_gate(ctx)

    # A flaky fetch must not strand the task at the submit gate — fail-open.
    assert env is None

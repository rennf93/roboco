"""_ensure_branch_for_task trust-but-verifies a pre-set branch_name (Defect A).

A ``branch_name`` set on the task is not proof the ref is on origin — a manual
field write, or a prior failed ``create_branch`` whose rollback didn't restore
``branch_name``, can leave the field set while the branch was never pushed.
Descendants then ``ls-remote`` this name, find it empty, and silently cut from
master via ``create_branch``'s fallback, breaking the hierarchy. The
short-circuit now probes origin and pushes the branch when confirmed missing;
an inconclusive probe fails soft. The claim rollback also restores
``branch_name`` so a failed first attempt can't leave the field half-set.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.services.git import GitService
from roboco.services.task import TaskService


def _service() -> TaskService:
    svc = TaskService.__new__(TaskService)
    svc.log = MagicMock()
    svc.session = MagicMock()
    # __new__ skips __init__, so the claim durability boundary's snapshot
    # bridge needs its own manual init here, same as log/session above.
    svc._pending_claim_snapshots = {}
    return svc


def _git_service() -> GitService:
    g = GitService.__new__(GitService)
    g.log = MagicMock()
    g.session = MagicMock()
    return g


# ---------------------------------------------------------------------------
# _ensure_branch_for_task: trust-but-verify a pre-set branch_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preset_branch_missing_on_remote_recreates() -> None:
    """branch_name set + ref confirmed absent → run _auto_create_branch (push)."""
    svc = _service()
    task = MagicMock(id=uuid4(), project_id=uuid4(), branch_name="feature/main_pm/x--y")
    object.__setattr__(
        svc, "_named_branch_missing_on_remote", AsyncMock(return_value=True)
    )
    auto = AsyncMock(return_value="feature/main_pm/x--y")
    object.__setattr__(svc, "_auto_create_branch", auto)

    out = await svc._ensure_branch_for_task(task, uuid4())

    assert out == "feature/main_pm/x--y"
    auto.assert_awaited_once()


@pytest.mark.asyncio
async def test_preset_branch_present_on_remote_skips_create() -> None:
    """branch_name set + ref present → return name, do not recreate."""
    svc = _service()
    task = MagicMock(id=uuid4(), project_id=uuid4(), branch_name="feature/main_pm/x--y")
    object.__setattr__(
        svc, "_named_branch_missing_on_remote", AsyncMock(return_value=False)
    )
    auto = AsyncMock(return_value="should-not-be-called")
    object.__setattr__(svc, "_auto_create_branch", auto)

    out = await svc._ensure_branch_for_task(task, uuid4())

    assert out == "feature/main_pm/x--y"
    auto.assert_not_awaited()


@pytest.mark.asyncio
async def test_preset_branch_inconclusive_probe_fails_soft() -> None:
    """branch_name set + probe inconclusive → return name, do not recreate.

    ``_named_branch_missing_on_remote`` returns False for an inconclusive
    probe (None), so the short-circuit returns the name without triggering a
    redundant full create — a transient network glitch can't fail the claim.
    """
    svc = _service()
    task = MagicMock(id=uuid4(), project_id=uuid4(), branch_name="feature/main_pm/x--y")
    object.__setattr__(
        svc, "_named_branch_missing_on_remote", AsyncMock(return_value=False)
    )
    auto = AsyncMock(return_value="should-not-be-called")
    object.__setattr__(svc, "_auto_create_branch", auto)

    out = await svc._ensure_branch_for_task(task, uuid4())

    assert out == "feature/main_pm/x--y"
    auto.assert_not_awaited()


@pytest.mark.asyncio
async def test_preset_branch_no_project_skips_verify() -> None:
    """branch_name set + no project_id (coordination/umbrella) → return name.

    A branchless coordination task carries no repo, so there is nothing to
    probe or push — the verify is gated on task.project_id.
    """
    svc = _service()
    task = MagicMock(id=uuid4(), project_id=None, branch_name="feature/main_pm/x")
    probe = AsyncMock(return_value=True)
    object.__setattr__(svc, "_named_branch_missing_on_remote", probe)
    auto = AsyncMock(return_value="should-not-be-called")
    object.__setattr__(svc, "_auto_create_branch", auto)

    out = await svc._ensure_branch_for_task(task, uuid4())

    assert out == "feature/main_pm/x"
    probe.assert_not_awaited()
    auto.assert_not_awaited()


# ---------------------------------------------------------------------------
# _named_branch_missing_on_remote: True only on confirmed-absent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_named_branch_missing_true_only_when_confirmed_absent() -> None:
    svc = _service()
    task = MagicMock(id=uuid4(), project_id=uuid4(), branch_name="feature/main_pm/x--y")
    project = MagicMock(slug="roboco-api")
    proj_svc = MagicMock()
    proj_svc.get = AsyncMock(return_value=project)
    git_svc = MagicMock()
    # absent → True; present → False; inconclusive (None) → False
    git_svc.branch_exists_on_remote = AsyncMock(side_effect=[False, True, None])
    with (
        patch(
            "roboco.services.project.get_project_service",
            MagicMock(return_value=proj_svc),
        ),
        patch("roboco.services.git.get_git_service", MagicMock(return_value=git_svc)),
    ):
        assert await svc._named_branch_missing_on_remote(task, uuid4()) is True
        assert await svc._named_branch_missing_on_remote(task, uuid4()) is False
        assert await svc._named_branch_missing_on_remote(task, uuid4()) is False


@pytest.mark.asyncio
async def test_named_branch_missing_false_when_project_unresolved() -> None:
    svc = _service()
    task = MagicMock(id=uuid4(), project_id=uuid4(), branch_name="feature/main_pm/x--y")
    proj_svc = MagicMock()
    proj_svc.get = AsyncMock(return_value=None)
    with patch(
        "roboco.services.project.get_project_service",
        MagicMock(return_value=proj_svc),
    ):
        assert await svc._named_branch_missing_on_remote(task, uuid4()) is False


# ---------------------------------------------------------------------------
# GitService.branch_exists_on_remote: True / False / None
# ---------------------------------------------------------------------------


def _run_git_result(stdout: str) -> MagicMock:
    res = MagicMock()
    res.stdout = stdout
    return res


@pytest.mark.asyncio
async def test_branch_exists_on_remote_present() -> None:
    g = _git_service()
    clone = MagicMock()
    object.__setattr__(g, "get_workspace", AsyncMock(return_value=clone))
    object.__setattr__(g, "_token_for_project", AsyncMock(return_value="tok"))
    object.__setattr__(
        g,
        "_run_git",
        AsyncMock(
            return_value=_run_git_result("abc123\trefs/heads/feature/main_pm/x--y\n")
        ),
    )
    assert (
        await g.branch_exists_on_remote("roboco-api", "feature/main_pm/x--y", uuid4())
        is True
    )


@pytest.mark.asyncio
async def test_branch_exists_on_remote_absent() -> None:
    g = _git_service()
    clone = MagicMock()
    object.__setattr__(g, "get_workspace", AsyncMock(return_value=clone))
    object.__setattr__(g, "_token_for_project", AsyncMock(return_value="tok"))
    object.__setattr__(g, "_run_git", AsyncMock(return_value=_run_git_result("")))
    assert (
        await g.branch_exists_on_remote("roboco-api", "feature/main_pm/x--y", uuid4())
        is False
    )


@pytest.mark.asyncio
async def test_branch_exists_on_remote_probe_error_fails_soft() -> None:
    g = _git_service()
    object.__setattr__(
        g, "get_workspace", AsyncMock(side_effect=RuntimeError("no workspace"))
    )
    object.__setattr__(g, "_token_for_project", AsyncMock(return_value="tok"))
    assert (
        await g.branch_exists_on_remote("roboco-api", "feature/main_pm/x--y", uuid4())
        is None
    )


# ---------------------------------------------------------------------------
# _provision_claim rollback restores branch_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_claim_rollback_restores_branch_name() -> None:
    """A failed _ensure_branch_for_task that set branch_name restores it.

    Simulates create_branch setting task.branch_name (the in-memory assignment
    in _create_branch_in_project) before a later step throws; the rollback
    must restore branch_name so a retried claim re-runs the create instead of
    short-circuiting on a half-set field.
    """
    svc = _service()
    agent_id = uuid4()
    task = MagicMock(
        id=uuid4(),
        project_id=uuid4(),
        branch_name=None,
        status=TaskStatus.PENDING,
        assigned_to=None,
        claimed_by=None,
        claimed_at=None,
        last_heartbeat_at=None,
        active_claimant_id=None,
    )
    agent = MagicMock()
    agent.role.value = "main_pm"

    object.__setattr__(svc, "_set_original_developer_context", MagicMock())

    async def _set_branch_then_raise(t: MagicMock, _aid: object) -> None:
        t.branch_name = "feature/main_pm/x--y"
        raise RuntimeError("push failed")

    object.__setattr__(
        svc, "_ensure_branch_for_task", AsyncMock(side_effect=_set_branch_then_raise)
    )
    object.__setattr__(svc, "_validate_and_set_status", MagicMock())
    object.__setattr__(svc, "_emit_status_transition_audit", MagicMock())
    object.__setattr__(svc, "_create_work_session_if_needed", AsyncMock())
    object.__setattr__(svc, "_inject_proactive_context", AsyncMock())
    object.__setattr__(svc, "_CLAIMABLE_STATUSES", {TaskStatus.PENDING})

    session = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.in_nested_transaction = MagicMock(return_value=True)
    svc.session = session

    snapshot = await svc._apply_claim_fields(task, agent, agent_id)
    with pytest.raises(RuntimeError, match="push failed"):
        await svc._provision_claim(task, agent, agent_id, snapshot)

    assert task.branch_name is None, "rollback must restore branch_name to None"


# ---------------------------------------------------------------------------
# _apply_claim_fields / _provision_claim durability boundary
# (mirrors PR #951's review-claim fix)
# ---------------------------------------------------------------------------


def _claim_fields_harness() -> tuple[TaskService, MagicMock, MagicMock]:
    """Shared setup for the durability-boundary tests below.

    Fresh cut (``branch_name=None``) so ``_should_inherit_base`` is False
    and ``_inherit_upstream_base`` never needs mocking.
    """
    svc = _service()
    task = MagicMock(
        id=uuid4(),
        project_id=uuid4(),
        branch_name=None,
        status=TaskStatus.PENDING,
        assigned_to=None,
        claimed_by=None,
        claimed_at=None,
        last_heartbeat_at=None,
        active_claimant_id=None,
    )
    agent = MagicMock()
    agent.role.value = "developer"

    object.__setattr__(svc, "_set_original_developer_context", MagicMock())
    object.__setattr__(svc, "_validate_and_set_status", MagicMock())
    object.__setattr__(svc, "_create_work_session_if_needed", AsyncMock())
    object.__setattr__(svc, "_inject_proactive_context", AsyncMock())
    object.__setattr__(svc, "_CLAIMABLE_STATUSES", {TaskStatus.PENDING})
    object.__setattr__(svc, "_background_tasks", set())
    return svc, task, agent


@pytest.mark.asyncio
async def test_apply_then_provision_commits_before_and_after_branch() -> None:
    """The durability boundary commits before AND right after the branch-
    creation network call, when not nested.

    Not-nested (mirrors ``claim(provision=True)``'s own sequencing): the row
    lock must be released by ``_apply_claim_fields`` before
    ``_ensure_branch_for_task``'s git I/O runs, so a client-side timeout
    during that call can't turn into a lock storm; and ``_provision_claim``
    commits again right after the branch lands (the Finding A fix) so a
    later failure (work session, upstream-base inherit) can't roll back an
    uncommitted branch_name out from under an already-durable CLAIMED row.
    """
    svc, task, agent = _claim_fields_harness()
    agent_id = uuid4()
    call_order: list[str] = []

    session = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.in_nested_transaction = MagicMock(return_value=False)
    session.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))
    svc.session = session

    async def _ensure_branch(_t: MagicMock, _aid: object) -> str:
        call_order.append("ensure_branch_for_task")
        return "feature/dev/x"

    object.__setattr__(
        svc, "_ensure_branch_for_task", AsyncMock(side_effect=_ensure_branch)
    )

    snapshot = await svc._apply_claim_fields(task, agent, agent_id)
    await svc._provision_claim(task, agent, agent_id, snapshot)

    assert call_order == ["commit", "ensure_branch_for_task", "commit"], (
        "claim fields must commit before the branch-creation network call,"
        " and the branch must commit again right after it lands"
    )
    expected_commit_count = 2
    assert session.commit.await_count == expected_commit_count


@pytest.mark.asyncio
async def test_apply_claim_fields_skips_commit_inside_savepoint() -> None:
    """Nested (mirrors the verb runner's composed claim/set_plan/start).

    ``Session.commit()`` always commits to the root transaction, so calling
    it while a savepoint is active would end that savepoint out from under
    ``_run_composed_actions`` and its next composed action would raise
    ``InvalidRequestError``. The boundary must no-op here instead, and stash
    the snapshot on ``_pending_claim_snapshots`` for the later, separate
    ``provision_claim()`` call.
    """
    svc, task, agent = _claim_fields_harness()
    agent_id = uuid4()

    session = MagicMock()
    session.flush = AsyncMock()
    session.in_nested_transaction = MagicMock(return_value=True)
    session.commit = AsyncMock()
    svc.session = session

    snapshot = await svc._apply_claim_fields(task, agent, agent_id)

    session.commit.assert_not_awaited()
    assert svc._pending_claim_snapshots[task.id] is snapshot

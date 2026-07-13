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
# _finalize_claim rollback restores branch_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_claim_rollback_restores_branch_name() -> None:
    """A failed _ensure_branch_for_task that set branch_name restores it.

    Simulates create_branch setting task.branch_name (the in-memory assignment
    in _create_branch_in_project) before a later step throws — the rollback
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
    svc.session = session

    with pytest.raises(RuntimeError, match="push failed"):
        await svc._finalize_claim(task, agent, agent_id)

    assert task.branch_name is None, "rollback must restore branch_name to None"

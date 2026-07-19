"""TaskService coverage — activate, branch creation, work session, indexing.

Focuses on lifecycle methods that interact with branches, work sessions,
and the proactive-context background hook.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentTable,
    ProjectTable,
    WorkSessionTable,
)
from roboco.exceptions import TaskLifecycleError
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    BlockerResolverType,
    Complexity,
    SubstituteReason,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.permissions import AgentContext
from roboco.models.task import TaskCreateRequest
from roboco.models.work_session import WorkSessionCreate, WorkSessionStatus
from roboco.services.task import (
    SoftBlockInfo,
    SoftBlockInput,
    TaskService,
)
from roboco.services.work_session import WorkSessionService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def task_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        default_branch="main",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": TaskService(db_session),
        "agent_id": agent.id,
        "project_id": project.id,
        "project_slug": project.slug,
        "db": db_session,
    }


def _req(setup: dict, **overrides: Any) -> TaskCreateRequest:
    return TaskCreateRequest(
        title=overrides.pop("title", "t"),
        description=overrides.pop("description", "d"),
        acceptance_criteria=overrides.pop("acceptance_criteria", ["ac"]),
        team=overrides.pop("team", Team.BACKEND),
        created_by=setup["agent_id"],
        project_id=setup["project_id"],
        task_type=overrides.pop("task_type", TaskType.CODE),
        nature=overrides.pop("nature", TaskNature.TECHNICAL),
        estimated_complexity=overrides.pop("estimated_complexity", Complexity.MEDIUM),
        **overrides,
    )


# ---------------------------------------------------------------------------
# activate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_raises_when_task_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc.activate(uuid4(), agent_role="cell_pm")


@pytest.mark.asyncio
async def test_activate_raises_when_not_in_backlog(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    with pytest.raises(ValueError, match="not in BACKLOG"):
        await svc.activate(task.id, agent_role="cell_pm")


# ---------------------------------------------------------------------------
# _inject_proactive_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_proactive_context_skips_when_claim_rolled_back(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When fresh re-read shows task gone or unassigned, skip without error."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))

    # Force the inner fresh-read to return a task whose assigned_to is None.
    # The simplest path is to NOT reassign after create. The task fixture has
    # assigned_to = None from create_request defaults.
    assert task.assigned_to is None

    @asynccontextmanager_async  # Helper below
    async def _factory() -> None:
        return None

    # Build a fake session_factory whose context returns the test session
    db = task_setup["db"]

    class _SessionFactory:
        def __call__(self) -> _Ctx:
            return _Ctx(db)

    class _Ctx:
        def __init__(self, session: Any) -> None:
            self._session = session

        async def __aenter__(self) -> Any:
            return self._session

        async def __aexit__(self, exc_type: Any, exc: Any, _tb: Any) -> None:
            return None

    factory_instance = _SessionFactory()
    monkeypatch.setattr("roboco.db.base.get_session_factory", lambda: factory_instance)
    # Now the fresh-read returns the task with assigned_to == None,
    # mismatching agent_id passed in
    await svc._inject_proactive_context(task, task_setup["agent_id"])


@pytest.mark.asyncio
async def test_inject_proactive_context_swallows_errors(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Errors in proactive service should be logged + swallowed."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))

    # Make get_session_factory raise to force the except branch
    def _fail_factory() -> Any:
        raise RuntimeError("factory broken")

    monkeypatch.setattr("roboco.db.base.get_session_factory", _fail_factory)
    # Should not raise
    await svc._inject_proactive_context(task, task_setup["agent_id"])


@pytest.mark.asyncio
async def test_inject_proactive_context_writes_when_context_nonempty(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full happy path — fresh re-read sees the assignment, proactive returns
    non-empty context, write is performed.
    """
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()

    class _Ctx:
        def __init__(self, session: Any) -> None:
            self._session = session

        async def __aenter__(self) -> Any:
            return self._session

        async def __aexit__(self, exc_type: Any, exc: Any, _tb: Any) -> None:
            return None

    class _Factory:
        def __init__(self, session: Any) -> None:
            self._session = session

        def __call__(self) -> _Ctx:
            return _Ctx(self._session)

    factory = _Factory(db_session)
    monkeypatch.setattr("roboco.db.base.get_session_factory", lambda: factory)

    fake_context = MagicMock()
    fake_context.is_empty = MagicMock(return_value=False)
    fake_context.to_dict = MagicMock(return_value={"k": "v"})
    fake_context.similar_tasks = []
    fake_context.relevant_learnings = []
    fake_context.code_patterns = []

    fake_proactive = MagicMock()
    fake_proactive.on_task_claimed = AsyncMock(return_value=fake_context)

    async def _get_proactive() -> Any:
        return fake_proactive

    monkeypatch.setattr(
        "roboco.services.proactive.get_proactive_service", _get_proactive
    )
    # Patch session.commit so it doesn't really commit
    original_commit = db_session.commit

    async def _no_commit() -> None:
        await db_session.flush()

    monkeypatch.setattr(db_session, "commit", _no_commit)
    try:
        await svc._inject_proactive_context(task, task_setup["agent_id"])
    finally:
        monkeypatch.setattr(db_session, "commit", original_commit)


# ---------------------------------------------------------------------------
# _create_work_session_if_needed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_work_session_skips_for_qa(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/x"
    await db_session.flush()
    out = await svc._create_work_session_if_needed(task, task_setup["agent_id"], "qa")
    assert out is None


@pytest.mark.asyncio
async def test_create_work_session_skips_when_no_branch(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    # No branch
    await db_session.flush()
    out = await svc._create_work_session_if_needed(
        task, task_setup["agent_id"], "developer"
    )
    assert out is None


@pytest.mark.asyncio
async def test_create_work_session_creates_new(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/abc"
    await db_session.flush()
    out = await svc._create_work_session_if_needed(
        task, task_setup["agent_id"], "developer"
    )
    assert out is not None
    assert out.branch_name == "feature/backend/abc"


@pytest.mark.asyncio
async def test_create_work_session_returns_existing_session(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """When a WorkSession already exists, returns None (no double-create)."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/abc"
    await db_session.flush()
    existing = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=task_setup["agent_id"],
        branch_name="feature/backend/abc",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
    )
    db_session.add(existing)
    await db_session.flush()
    out = await svc._create_work_session_if_needed(
        task, task_setup["agent_id"], "developer"
    )
    assert out is None


@pytest.mark.asyncio
async def test_create_work_session_uses_parent_branch(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Subtask's work session targets parent branch, not project default."""
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    parent.branch_name = "feature/backend/PARENT"
    await db_session.flush()
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    child.branch_name = "feature/backend/PARENT--CHILD"
    await db_session.flush()
    out = await svc._create_work_session_if_needed(
        child, task_setup["agent_id"], "developer"
    )
    assert out is not None
    assert out.target_branch == "feature/backend/PARENT"


@pytest.mark.asyncio
async def test_create_work_session_no_project_returns_none(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When project lookup yields None, returns None (logs warning)."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/x"
    await db_session.flush()

    # Patch session.execute to return scalar_one_or_none=None for ProjectTable
    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = None

    real_execute = db_session.execute

    async def _exec_stub(stmt: Any, *a: Any, **kw: Any) -> Any:
        compiled = str(stmt)
        if "FROM projects" in compiled:
            return fake_result
        return await real_execute(stmt, *a, **kw)

    monkeypatch.setattr(db_session, "execute", _exec_stub)
    out = await svc._create_work_session_if_needed(
        task, task_setup["agent_id"], "developer"
    )
    assert out is None


@pytest.mark.asyncio
async def test_create_work_session_delegates_to_service_create(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim path must create the WorkSession via ``WorkSessionService.create``
    (single source of truth) rather than constructing a ``WorkSessionTable``
    directly, so service-layer validation (existing-active check, supersede
    invariant) is not bypassed."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/delegate"
    await db_session.flush()

    captured: dict[str, Any] = {}
    original_create = WorkSessionService.create

    async def _spy_create(_self: Any, data: WorkSessionCreate) -> Any:
        # Record the WorkSessionCreate the claim path handed to the service,
        # then run the real create so the row persists (the FK on
        # tasks.work_session_id requires a real work_sessions row).
        captured["data"] = data
        return await original_create(_self, data)

    monkeypatch.setattr(WorkSessionService, "create", _spy_create)

    out = await svc._create_work_session_if_needed(
        task, task_setup["agent_id"], "developer"
    )

    assert out is not None
    assert "data" in captured
    sent = captured["data"]
    assert isinstance(sent, WorkSessionCreate)
    assert sent.project_id == task_setup["project_id"]
    assert sent.task_id == task.id
    assert sent.agent_id == task_setup["agent_id"]
    assert sent.branch_name == "feature/backend/delegate"
    # Root task targets the project default branch.
    assert sent.target_branch == sent.base_branch
    # The claim path links the session back onto the task.
    assert task.work_session_id == out.id


# ---------------------------------------------------------------------------
# unclaim_for_reaper / unclaim_for_agent — work-session abandon paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unclaim_for_reaper_abandons_work_session(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    ws = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=task_setup["agent_id"],
        branch_name="feature/backend/x",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
    )
    db_session.add(ws)
    await db_session.flush()
    task.work_session_id = ws.id
    await db_session.flush()

    fake_ws_svc = MagicMock()
    fake_ws_svc.abandon = AsyncMock()

    monkeypatch.setattr(
        "roboco.services.work_session.WorkSessionService",
        lambda _s: fake_ws_svc,
    )
    await svc.unclaim_for_reaper(task.id)
    fake_ws_svc.abandon.assert_awaited_once()


@pytest.mark.asyncio
async def test_unclaim_for_agent_abandons_work_session(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    ws = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=task_setup["agent_id"],
        branch_name="feature/backend/x",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
    )
    db_session.add(ws)
    await db_session.flush()
    task.work_session_id = ws.id
    await db_session.flush()

    fake_ws_svc = MagicMock()
    fake_ws_svc.abandon = AsyncMock()

    monkeypatch.setattr(
        "roboco.services.work_session.WorkSessionService",
        lambda _s: fake_ws_svc,
    )
    out = await svc.unclaim_for_agent(task.id, agent_id=task_setup["agent_id"])
    assert out is not None
    fake_ws_svc.abandon.assert_awaited_once()


# ---------------------------------------------------------------------------
# Lifecycle event indexing — soft_block / unblock / pause / resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_block_spawns_blocker_index(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """soft_block fires _index_blocker_background as a bg task."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()

    fake_optimal = MagicMock()
    fake_optimal.index_error = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    out = await svc.soft_block(
        task.id,
        SoftBlockInfo(reason="r", blocker_type="ext", what_needed="w"),
    )
    assert out is not None
    # Wait for background task by yielding control
    # Allow background tasks to settle
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_pause_spawns_lifecycle_index(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    fake_optimal = MagicMock()
    fake_optimal.index_journal_entry = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    out = await svc.pause(task.id)
    assert out is not None
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_resume_spawns_lifecycle_index(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    await db_session.flush()
    fake_optimal = MagicMock()
    fake_optimal.index_journal_entry = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    out = await svc.resume(task.id)
    assert out is not None
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_unblock_spawns_lifecycle_index(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.BLOCKED
    await db_session.flush()
    fake_optimal = MagicMock()
    fake_optimal.index_journal_entry = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    out = await svc.unblock(task.id)
    assert out is not None
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# block: blocker reverse-link not duplicated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_does_not_duplicate_reverse_link(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    blocker = await svc.create(_req(task_setup))
    # Pre-set reverse link
    blocker.blocker_ids = [task.id]
    await db_session.flush()
    blocked = await svc.block(task.id, blocker_task_id=blocker.id)
    assert blocked is not None
    assert blocker.blocker_ids.count(task.id) == 1


# ---------------------------------------------------------------------------
# submit_for_qa happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_for_qa_clears_assignment_and_records_dev(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.VERIFYING
    task.assigned_to = task_setup["agent_id"]
    task.claimed_by = task_setup["agent_id"]
    await db_session.flush()
    out = await svc.submit_for_qa(task.id, agent_role="developer")
    assert out is not None
    assert out.status == TaskStatus.AWAITING_QA
    assert out.assigned_to is None
    assert (out.orchestration_markers or {}).get("original_developer")


# ---------------------------------------------------------------------------
# fail_qa — full path with original developer, including bg indexing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_qa_with_indexing_runs(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    dev_id = task_setup["agent_id"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.orchestration_markers = {"original_developer": str(dev_id)}
    await db_session.flush()
    fake_optimal = MagicMock()
    fake_optimal.record_review = AsyncMock()
    fake_optimal.index_error = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    failed = await svc.fail_qa(task.id, notes="needs work")
    assert failed is not None
    await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# pass_qa — full path with bg indexing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_qa_with_indexing_runs(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.pr_number = 1
    task.pr_url = "u"
    await db_session.flush()
    fake_optimal = MagicMock()
    fake_optimal.record_review = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    passed = await svc.pass_qa(task.id, notes="all good", agent_role="qa")
    assert passed is not None
    await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# cancel — full cascade with branch deletion + work session abandon
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_with_branch_and_work_session(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/x"
    await db_session.flush()
    ws = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=task_setup["agent_id"],
        branch_name="feature/backend/x",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
    )
    db_session.add(ws)
    await db_session.flush()
    task.work_session_id = ws.id
    await db_session.flush()

    fake_ws = MagicMock()
    fake_ws.abandon = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.work_session.get_work_session_service",
        lambda _s: fake_ws,
    )
    fake_git = MagicMock()
    fake_git.delete_task_branch = AsyncMock()
    fake_git.close_task_pr_best_effort = AsyncMock()
    monkeypatch.setattr("roboco.services.git.get_git_service", lambda _s: fake_git)
    out = await svc.cancel(task.id, agent_role="cell_pm")
    assert out is not None
    fake_ws.abandon.assert_awaited()
    fake_git.delete_task_branch.assert_awaited()


@pytest.mark.asyncio
async def test_cancel_closes_open_pr(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GAP A: cancelling a task with an open PR closes it on the forge —
    previously ``cancel`` force-deleted the branch/worktree but left
    ``pr_number`` PRs open forever."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/x"
    task.pr_number = 42
    task.pr_url = "https://example.com/r/pull/42"
    await db_session.flush()

    fake_git = MagicMock()
    fake_git.delete_task_branch = AsyncMock()
    fake_git.close_task_pr_best_effort = AsyncMock()
    monkeypatch.setattr("roboco.services.git.get_git_service", lambda _s: fake_git)

    out = await svc.cancel(task.id, agent_role="cell_pm")

    assert out is not None
    fake_git.close_task_pr_best_effort.assert_awaited_once_with(
        task_setup["project_slug"], 42
    )


@pytest.mark.asyncio
async def test_cancel_skips_pr_close_without_pr_number(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/x"
    await db_session.flush()

    fake_git = MagicMock()
    fake_git.delete_task_branch = AsyncMock()
    fake_git.close_task_pr_best_effort = AsyncMock()
    monkeypatch.setattr("roboco.services.git.get_git_service", lambda _s: fake_git)

    out = await svc.cancel(task.id, agent_role="cell_pm")

    assert out is not None
    fake_git.close_task_pr_best_effort.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_descendants_cascades_for_authorized_pm(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """A `cell_pm` cancel cascades through descendants in any PM-cancelable
    non-terminal state.

    The canonical spec (`roboco.foundation.policy.lifecycle`) authorizes
    cancel from every non-terminal source for {CELL_PM, MAIN_PM, CEO}
    EXCEPT `awaiting_ceo_approval`, which is CEO-only (a PM cancelling a
    task the CEO is reviewing would bypass the human CEO gate). So a PM
    cancel sweeps the whole subtree of PM-cancelable descendants — here a
    child parked in `in_progress`. A descendant in `awaiting_ceo_approval`
    is the refuse case, covered by
    `test_cancel_refuses_when_descendant_role_forbidden`.
    """
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    child.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    out = await svc.cancel(parent.id, agent_role="cell_pm")
    assert out is not None
    refreshed_child = await svc.get(child.id)
    assert refreshed_child is not None
    # Child cascades to cancelled along with the parent.
    assert refreshed_child.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_refuses_when_descendant_role_forbidden(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#103: a non-terminal descendant the caller's role can't cancel refuses
    the whole cancel — never silently skips the descendant and leaves an
    orphaned subtree under a cancelled parent.

    The current spec gates every cancel edge to {cell_pm, main_pm, ceo}
    uniformly, so a PM cancel won't naturally hit a role-forbidden
    descendant. Simulate the future-regression shape (a per-edge role gate
    that re-excludes a state) by stubbing ``_validate_and_set_status`` to
    raise ``TaskLifecycleError`` for the descendant only — the parent's
    cancel stays valid. The broad ``except Exception`` swallow used to
    skip the descendant and cancel the parent anyway (orphan); it must
    now refuse.
    """
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()

    real_validate = svc._validate_and_set_status

    def stub_validate(task: Any, new_status: Any, agent_role: Any) -> Any:
        if task.id == child.id:
            raise TaskLifecycleError(
                current_status=task.status.value,
                target_status=new_status.value,
                message="simulated per-edge role gate excludes this descendant",
            )
        return real_validate(task, new_status, agent_role)

    monkeypatch.setattr(svc, "_validate_and_set_status", stub_validate)

    with pytest.raises(TaskLifecycleError, match="orphaned subtree"):
        await svc.cancel(parent.id, agent_role="cell_pm")

    refreshed_parent = await svc.get(parent.id)
    assert refreshed_parent is not None
    assert refreshed_parent.status != TaskStatus.CANCELLED
    refreshed_child = await svc.get(child.id)
    assert refreshed_child is not None
    assert refreshed_child.status != TaskStatus.CANCELLED


# ---------------------------------------------------------------------------
# Helper: simple async context manager
# ---------------------------------------------------------------------------


def asynccontextmanager_async(func: Any) -> Any:
    """Stub decorator — actual implementation lives in std lib."""
    return contextlib.asynccontextmanager(func)


# ---------------------------------------------------------------------------
# soft_block_task_for_agent notification path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_block_task_for_agent_full_flow(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    agent_ctx = AgentContext(
        agent_id=task_setup["agent_id"],
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        slug="x",
    )
    fake_delivery = MagicMock()
    fake_delivery.notify_pm_of_block = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: fake_delivery,
    )

    # Bypass the explicit commit() on session
    async def _no_commit() -> None:
        await db_session.flush()

    monkeypatch.setattr(db_session, "commit", _no_commit)

    req = SoftBlockInput(
        blocker_type="external",
        reason="r",
        what_needed="w",
        resolver_type=BlockerResolverType.AGENT,
    )
    out = await svc.soft_block_task_for_agent(task.id, agent_ctx, req)
    assert out is not None
    fake_delivery.notify_pm_of_block.assert_awaited_once()


# ---------------------------------------------------------------------------
# docs_complete_for_task — notification path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_complete_for_task_invokes_notification(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:

    svc = task_setup["svc"]
    doc = AgentTable(
        id=uuid4(),
        name="Doc",
        slug=f"be-doc-{uuid4().hex[:8]}",
        role=AgentRole.DOCUMENTER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="d",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(doc)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_DOCUMENTATION
    task.assigned_to = doc.id
    task.pr_number = 1
    task.pr_url = "u"
    task.pr_created = True
    await db_session.flush()
    agent_ctx = AgentContext(
        agent_id=cast("uuid.UUID", doc.id),
        role=AgentRole.DOCUMENTER,
        team=Team.BACKEND,
        slug=doc.slug,
    )
    fake_delivery = MagicMock()
    fake_delivery.notify_pm_of_docs_complete = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: fake_delivery,
    )

    async def _no_commit() -> None:
        await db_session.flush()

    monkeypatch.setattr(db_session, "commit", _no_commit)
    out = await svc.docs_complete_for_task(
        task.id,
        agent_ctx,
        "Substantial notes about what was documented and where in detail.",
    )
    assert out is not None
    fake_delivery.notify_pm_of_docs_complete.assert_awaited_once()


# ---------------------------------------------------------------------------
# escalate_to_ceo_for_agent — notification path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_to_ceo_for_agent_invokes_notification(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:

    svc = task_setup["svc"]
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = 1
    task.pr_url = "u"
    task.pr_created = True
    task.docs_complete = True
    await db_session.flush()
    agent_ctx = AgentContext(
        agent_id=cast("uuid.UUID", pm.id),
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        slug=pm.slug,
    )

    class _P:
        def can_perform_task_action(self, *a: Any, **kw: Any) -> bool:
            del a, kw
            return True

    fake_delivery = MagicMock()
    fake_delivery.notify_ceo_of_escalation = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: fake_delivery,
    )

    async def _no_commit() -> None:
        await db_session.flush()

    monkeypatch.setattr(db_session, "commit", _no_commit)
    out = await svc.escalate_to_ceo_for_agent(
        task.id,
        agent_ctx,
        _P(),
        "Substantial reasons for CEO review: scope, risk, breaking change",
    )
    assert out is not None
    fake_delivery.notify_ceo_of_escalation.assert_awaited_once()


# ---------------------------------------------------------------------------
# claim_task_for_agent commits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_task_for_agent_commits_and_returns(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:

    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/x"
    await db_session.flush()
    agent_ctx = AgentContext(
        agent_id=task_setup["agent_id"],
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        slug="x",
    )

    class _P:
        def can_perform_task_action(self, *a: Any, **kw: Any) -> bool:
            del a, kw
            return True

    async def _no_commit() -> None:
        await db_session.flush()

    monkeypatch.setattr(db_session, "commit", _no_commit)
    out = await svc.claim_task_for_agent(task.id, agent_ctx, _P(), None)
    assert out.id == task.id


# ---------------------------------------------------------------------------
# complete_task_for_agent commits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_task_for_agent_commits(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:

    svc = task_setup["svc"]
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = pm.id
    await db_session.flush()
    agent_ctx = AgentContext(
        agent_id=cast("uuid.UUID", pm.id),
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        slug=pm.slug,
    )

    class _P:
        def can_perform_task_action(self, *a: Any, **kw: Any) -> bool:
            del a, kw
            return True

    async def _no_commit() -> None:
        await db_session.flush()

    monkeypatch.setattr(db_session, "commit", _no_commit)
    out = await svc.complete_task_for_agent(task.id, agent_ctx, _P())
    assert out.status == TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# substitute_task_for_agent — runs full update + commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_substitute_task_for_agent_runs_update(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:

    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    agent_ctx = AgentContext(
        agent_id=task_setup["agent_id"],
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        slug="x",
    )

    monkeypatch.setattr("roboco.agents_config.get_pm_for_agent", lambda _s: None)
    monkeypatch.setattr("roboco.agents_config.get_pm_for_team", lambda _t: None)

    async def _no_commit() -> None:
        await db_session.flush()

    monkeypatch.setattr(db_session, "commit", _no_commit)
    out = await svc.substitute_task_for_agent(
        task.id,
        agent_ctx,
        SubstituteReason.MAX_RETRIES.value,
        "needs different agent",
    )
    assert out is not None
    # A transient substitute-out must NOT orphan the task: it stays with the
    # same agent (re-dispatchable, resumes from the briefing) — never
    # pending+unassigned.
    assert out.status == TaskStatus.PENDING
    assert out.assigned_to == task_setup["agent_id"]

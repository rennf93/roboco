"""Cell-team PM-owned children must be assigned to their cell PM.

A descendant task whose ``team`` is a cell team and whose ``task_type`` is a
non-code work type (``planning`` / ``research`` / ``administrative`` /
``documentation`` / ``design``) belongs to that cell's PM. Assigning it to
``main-pm`` (or any other mismatched owner) deadlocks the escalation chain,
because the Main PM escalates up to the board, which cannot own cell
coordination work. The service-layer invariant redirects such assignments to
the correct cell PM at reassign / reassign-active-claim time and writes an
``[ASSIGNMENT REDIRECTED]`` line to ``dev_notes`` so the audit is visible in
the task body, not only in logs. The create path does NOT redirect (per the
decision that reassign is the single backstop) and is regression-tested as
such. Direct ORM writes from the escalation chain are out of scope.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from roboco.models.base import TaskStatus, TaskType, Team
from roboco.services.task import TaskService, _is_cell_pm_owned_task


def _bind(svc: TaskService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _service() -> TaskService:
    session = MagicMock()
    session.flush = AsyncMock()
    # reassign_active_claim now retargets the agent-side claim marker
    # (_retarget_agent_claim), which reads agent rows via session.get —
    # default to "no matching row" so tests that don't care about the
    # agent side effect stay a no-op there.
    session.get = AsyncMock(return_value=None)
    return TaskService(session)


def _task(
    *,
    team: Team,
    task_type: TaskType,
    assigned_to: UUID | None = None,
    status: TaskStatus = TaskStatus.CLAIMED,
    parent_task_id: object = "USE_DEFAULT",
) -> MagicMock:
    """Build a task-shaped mock. ``parent_task_id`` defaults to a fresh UUID
    (a descendant); pass an explicit ``None`` for a root task. The sentinel
    avoids the truthiness collision where ``None`` and "use a default" both
    look the same to ``or`` / ternary ``if-else``.
    """
    resolved_parent = uuid4() if parent_task_id == "USE_DEFAULT" else parent_task_id
    return MagicMock(
        id=uuid4(),
        parent_task_id=resolved_parent,
        team=team,
        task_type=task_type,
        assigned_to=assigned_to,
        claimed_by=assigned_to,
        active_claimant_id=assigned_to,
        dev_notes="",
        status=status,
    )


# ---------------------------------------------------------------------------
# Pure predicate — every non-code cell-team child type is PM-owned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task_type",
    [
        TaskType.PLANNING,
        TaskType.RESEARCH,
        TaskType.ADMINISTRATIVE,
        TaskType.DOCUMENTATION,
        TaskType.DESIGN,
    ],
)
def test_cell_non_code_child_is_pm_owned(task_type: TaskType) -> None:
    """Every non-code cell-team child task is PM-owned by its cell PM."""
    task = _task(team=Team.BACKEND, task_type=task_type)
    assert _is_cell_pm_owned_task(task) is True


def test_cell_code_child_is_not_pm_owned() -> None:
    """Leaf code work is delegated by the cell PM to a developer, not owned
    by the cell PM — it is NOT subject to the redirect."""
    task = _task(team=Team.BACKEND, task_type=TaskType.CODE)
    assert _is_cell_pm_owned_task(task) is False


def test_root_cell_planning_is_not_pm_owned() -> None:
    """Roots are Main-PM coordination work; only descendants are PM-owned."""
    task = _task(
        team=Team.BACKEND,
        task_type=TaskType.PLANNING,
        parent_task_id=None,
    )
    assert _is_cell_pm_owned_task(task) is False


def test_main_pm_team_planning_is_not_pm_owned() -> None:
    """The invariant is cell-team-specific; main-pm team tasks skip it."""
    task = _task(team=Team.MAIN_PM, task_type=TaskType.PLANNING)
    assert _is_cell_pm_owned_task(task) is False


# ---------------------------------------------------------------------------
# Service-layer redirect at reassign — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassign_redirects_main_pm_to_cell_pm_for_planning_child() -> None:
    svc = _service()
    be_pm_id = uuid4()
    main_pm_id = uuid4()
    task = _task(
        team=Team.BACKEND,
        task_type=TaskType.PLANNING,
        assigned_to=main_pm_id,
    )
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))
    _bind(
        svc,
        "cell_pm_for_team",
        AsyncMock(return_value=MagicMock(id=be_pm_id, slug="be-pm")),
    )

    result = await svc.reassign(task.id, main_pm_id)

    assert result is task
    assert task.assigned_to == be_pm_id
    assert task.claimed_by == be_pm_id
    # Reassign now writes the audit line so the redirect is visible in the
    # task body, not just in logs.
    assert "[ASSIGNMENT REDIRECTED]" in task.dev_notes
    assert "be-pm" in task.dev_notes


@pytest.mark.asyncio
async def test_reassign_keeps_cell_pm_for_planning_child() -> None:
    """A cell PM request on a cell-team PM-owned task is a no-op redirect."""
    svc = _service()
    be_pm_id = uuid4()
    task = _task(
        team=Team.BACKEND,
        task_type=TaskType.PLANNING,
        assigned_to=None,
    )
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))
    _bind(
        svc,
        "cell_pm_for_team",
        AsyncMock(return_value=MagicMock(id=be_pm_id, slug="be-pm")),
    )

    result = await svc.reassign(task.id, be_pm_id)

    assert result is task
    assert task.assigned_to == be_pm_id
    # No redirect → no audit line.
    assert "[ASSIGNMENT REDIRECTED]" not in task.dev_notes


@pytest.mark.asyncio
async def test_reassign_active_claim_redirects_main_pm_to_cell_pm() -> None:
    svc = _service()
    be_pm_id = uuid4()
    main_pm_id = uuid4()
    task = _task(
        team=Team.BACKEND,
        task_type=TaskType.RESEARCH,
        assigned_to=main_pm_id,
    )
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))
    _bind(
        svc,
        "cell_pm_for_team",
        AsyncMock(return_value=MagicMock(id=be_pm_id, slug="be-pm")),
    )

    result = await svc.reassign_active_claim(task.id, main_pm_id)

    assert result is task
    assert task.assigned_to == be_pm_id
    assert task.claimed_by == be_pm_id
    assert task.active_claimant_id == be_pm_id
    assert "[ASSIGNMENT REDIRECTED]" in task.dev_notes


# ---------------------------------------------------------------------------
# Service-layer redirect — failure modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassign_queues_task_when_no_cell_pm_exists() -> None:
    """When no cell PM agent row exists for the team, queue the task (no
    assignee) and log an error rather than silently routing to the caller's
    requested assignee. The task stays PENDING until the agents table is
    repaired; this is observable in logs and dev_notes.
    """
    svc = _service()
    main_pm_id = uuid4()
    task = _task(
        team=Team.BACKEND,
        task_type=TaskType.PLANNING,
        assigned_to=main_pm_id,
    )
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))
    _bind(svc, "cell_pm_for_team", AsyncMock(return_value=None))

    result = await svc.reassign(task.id, main_pm_id)

    assert result is task
    # Queued: assignee cleared so the orchestrator does not respawn into a
    # misrouted hand-off.
    assert task.assigned_to is None
    assert task.claimed_by is None
    assert "[ASSIGNMENT REDIRECTED]" in task.dev_notes
    assert "queued" in task.dev_notes


@pytest.mark.asyncio
async def test_reassign_active_claim_queues_when_no_cell_pm_exists() -> None:
    """Same missing-cell-PM fallback for the active-claim path."""
    svc = _service()
    main_pm_id = uuid4()
    task = _task(
        team=Team.BACKEND,
        task_type=TaskType.DESIGN,
        assigned_to=main_pm_id,
    )
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))
    _bind(svc, "cell_pm_for_team", AsyncMock(return_value=None))

    result = await svc.reassign_active_claim(task.id, main_pm_id)

    assert result is task
    assert task.assigned_to is None
    assert task.claimed_by is None
    assert task.active_claimant_id is None
    assert "[ASSIGNMENT REDIRECTED]" in task.dev_notes


@pytest.mark.asyncio
async def test_reassign_board_divert_fires_before_cell_pm_redirect() -> None:
    """The board-divert guard must run BEFORE the cell-PM redirect. If a
    board agent is asked to own a cell task, the divert-to-pool path is the
    one that fires — not a cell-PM redirect that never matches because the
    board agent is not the cell PM. This test exercises the ordering so a
    future refactor that reorders the guards fails loudly.
    """
    svc = _service()
    board_agent_id = uuid4()
    task = _task(
        team=Team.BACKEND,
        task_type=TaskType.PLANNING,
        assigned_to=board_agent_id,
    )
    _bind(svc, "get", AsyncMock(return_value=task))
    # Board guard says YES, this is a board agent.
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=True))
    # Cell PM lookup must NOT be reached — if it is, the test fails because
    # the AsyncMock has no return_value configured.
    _bind(
        svc,
        "cell_pm_for_team",
        AsyncMock(
            side_effect=AssertionError(
                "cell_pm_for_team must not run when board-divert fires first"
            )
        ),
    )
    diverted = AsyncMock()
    _bind(svc, "_divert_owned_task_to_pool", diverted)

    result = await svc.reassign(task.id, board_agent_id)

    assert result is task
    diverted.assert_awaited_once()
    # Board-divert path returned before the cell-PM redirect mutated fields.
    assert task.assigned_to == board_agent_id


# ---------------------------------------------------------------------------
# Regression — the create path does NOT redirect
# ---------------------------------------------------------------------------


def test_create_does_not_redirect_misassigned_cell_planning_child() -> None:
    """Regression for the create-path backstop decision. The decision (see
    audit, Gap 1) was: the create path no longer redirects to the cell PM,
    because the reassign path is the single backstop and a second redirect
    on create is duplicate coverage. If this test ever fails, someone has
    re-added the create-path redirect and should be challenged on why.
    """
    svc = _service()
    # If a `_redirect_cell_team_pm_task` method ever reappears, this assert
    # should make the failure obvious in the diff.
    assert not hasattr(svc, "_redirect_cell_team_pm_task"), (
        "create-path redirect was re-added; remove it (see audit Gap 1)."
    )

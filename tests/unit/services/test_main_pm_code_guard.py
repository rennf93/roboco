"""``main_pm`` + ``code`` must never coexist — the impossibility guard.

The 2026-06-27 MegaTask meltdown traced to a Main-PM-owned root-subtask that
was ``task_type=code``: the git/PR/review layer treated it as a code root
(branch + PR + ``submit_root`` + the in-path ``pr_review`` gate + complete),
while the ownership/dispatch layer treated it as coordination (owned /
claimed / submitted / completed by the Main PM). The two layers never
reconciled — a ``pr_fail`` on the assembled code landed on a coordinator with
no code verb, who re-submitted the unchanged root → infinite loop. The CEO's
fix: *"It should be impossible to draft a task where main pm and task type
code COEXIST."*

This suite pins the layered backstop that closes the invariant at every site
where the combo can be created or re-handed:

* ``TaskService.create`` rejects ``main_pm`` + ``code`` (the HTTP-route /
  direct-create backstop; intake coerces ``code``→``planning``).
* ``approve_and_start`` retypes a board-routed code task to ``planning`` when
  it hands it to Main PM (the board→main-pm handoff is where a project code
  task would otherwise become ``main_pm`` + ``code``).
* ``apply_escalation`` / ``reassign`` / ``reassign_active_claim`` divert a
  ``main_pm`` + ``code`` task handed to a Main-PM target to the pool — but
  STILL allow reassigning it to a cell dev (the correct remediation).
* ``claim_task_for_agent`` rejects a Main-PM agent claiming a CODE task in an
  execution state (claiming = owning through the lifecycle, not delegating),
  while leaving the ``awaiting_pm_review`` review-claim path untouched (C8).

The single predicate every layer consults is
``roboco.foundation.policy.batch.main_pm_cannot_own_code`` (accepts ORM enums
or their ``.value`` strings); these tests exercise the service-layer sites
that call it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.base import (
    AgentRole,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.models.task import TaskCreateRequest
from roboco.services.base import UnauthorizedError, ValidationError
from roboco.services.task import TaskService


def _bind(svc: TaskService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _service() -> TaskService:
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return TaskService(session)


def _task(
    *,
    team: Team,
    task_type: TaskType,
    status: TaskStatus = TaskStatus.PENDING,
    assigned_to: object = None,
    board_review_complete: bool = True,
) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        team=team,
        task_type=task_type,
        status=status,
        assigned_to=assigned_to,
        claimed_by=assigned_to,
        active_claimant_id=assigned_to,
        blocker_raised_by=None,
        board_review_complete=board_review_complete,
        dev_notes="",
    )


def _agent(role: AgentRole = AgentRole.DEVELOPER) -> MagicMock:
    return MagicMock(role=role, agent_id=uuid4())


def _perms() -> MagicMock:
    p = MagicMock()
    p.can_perform_task_action = MagicMock(return_value=True)
    return p


# ---------------------------------------------------------------------------
# Site 1 — TaskService.create backstop (invariant #1: the combo on the task)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rejects_main_pm_plus_code() -> None:
    svc = TaskService(
        MagicMock(add=MagicMock(), flush=AsyncMock(), execute=AsyncMock())
    )
    req = TaskCreateRequest(
        title="rogue main-pm code root",
        description="should not persist",
        acceptance_criteria=["ship it"],
        team=Team.MAIN_PM,
        created_by=uuid4(),
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        project_id=uuid4(),
    )
    with pytest.raises(ValidationError, match="MAIN_PM"):
        await svc.create(req)


@pytest.mark.asyncio
async def test_create_allows_main_pm_plus_planning() -> None:
    # The backstop is the team+type combo, not main_pm alone — a Main-PM
    # coordination root typed planning is the canonical shape, not a mismatch.
    svc = TaskService(
        MagicMock(add=MagicMock(), flush=AsyncMock(), execute=AsyncMock())
    )
    req = TaskCreateRequest(
        title="main-pm coordination root",
        description="fine",
        acceptance_criteria=["coordinate the cells"],
        team=Team.MAIN_PM,
        created_by=uuid4(),
        task_type=TaskType.PLANNING,
        nature=TaskNature.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        project_id=uuid4(),
    )
    task = await svc.create(req)
    assert task.task_type == TaskType.PLANNING


# ---------------------------------------------------------------------------
# Site 2 — approve_and_start retype (board→main-pm handoff)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_and_start_retypes_code_to_planning_for_main_pm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _service()
    main_pm_agent = MagicMock(id=uuid4(), slug="main-pm", role=AgentRole.MAIN_PM)
    agent_svc = MagicMock()
    agent_svc.get_by_slug = AsyncMock(return_value=main_pm_agent)
    monkeypatch.setattr(
        "roboco.services.agent.get_agent_service", lambda _session: agent_svc
    )
    task = _task(
        team=Team.BOARD,
        task_type=TaskType.CODE,
        status=TaskStatus.PENDING,
        board_review_complete=True,
    )
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_activate_batch_root_subtasks", AsyncMock())
    _bind(svc, "_emit_task_event", AsyncMock())

    await svc.approve_and_start(task.id)

    # Handed to Main PM (team set) AND retyped off code in the same write.
    assert task.team == Team.MAIN_PM
    assert task.task_type == TaskType.PLANNING


@pytest.mark.asyncio
async def test_approve_and_start_leaves_planning_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _service()
    main_pm_agent = MagicMock(id=uuid4(), slug="main-pm", role=AgentRole.MAIN_PM)
    agent_svc = MagicMock()
    agent_svc.get_by_slug = AsyncMock(return_value=main_pm_agent)
    monkeypatch.setattr(
        "roboco.services.agent.get_agent_service", lambda _session: agent_svc
    )
    task = _task(
        team=Team.BOARD,
        task_type=TaskType.PLANNING,
        status=TaskStatus.PENDING,
        board_review_complete=True,
    )
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_activate_batch_root_subtasks", AsyncMock())
    _bind(svc, "_emit_task_event", AsyncMock())

    await svc.approve_and_start(task.id)

    assert task.team == Team.MAIN_PM
    assert task.task_type == TaskType.PLANNING


# ---------------------------------------------------------------------------
# Site 3 — apply_escalation: divert a main_pm+code task → main-pm target to pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_escalation_diverts_main_pm_code_to_main_pm_target() -> None:
    svc = _service()
    main_pm_target = uuid4()
    task = _task(
        team=Team.MAIN_PM,
        task_type=TaskType.CODE,
        status=TaskStatus.IN_PROGRESS,
        assigned_to=uuid4(),
    )
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))
    _bind(svc, "_is_main_pm_agent", AsyncMock(return_value=True))
    release_mock = AsyncMock()
    _bind(svc, "_release_code_task_to_pool", release_mock)

    await svc.apply_escalation(
        task=task,
        target_agent_id=main_pm_target,
        escalator_slug="cell-pm",
        target_slug="main-pm",
        reason="blocked",
    )

    release_mock.assert_awaited_once()
    assert task.assigned_to != main_pm_target


@pytest.mark.asyncio
async def test_apply_escalation_does_not_divert_main_pm_code_to_cell_dev() -> None:
    # The correct remediation for a legacy main_pm+code task is to reassign it
    # to a cell dev who can actually fix the code — the guard must NOT block
    # that, only block re-handing it BACK to a Main-PM target.
    svc = _service()
    cell_dev_target = uuid4()
    task = _task(
        team=Team.MAIN_PM,
        task_type=TaskType.CODE,
        status=TaskStatus.IN_PROGRESS,
        assigned_to=uuid4(),
    )
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))
    _bind(svc, "_is_main_pm_agent", AsyncMock(return_value=False))
    release_mock = AsyncMock()
    _bind(svc, "_release_code_task_to_pool", release_mock)
    _bind(svc, "_emit_task_event", AsyncMock())

    await svc.apply_escalation(
        task=task,
        target_agent_id=cell_dev_target,
        escalator_slug="main-pm",
        target_slug="be-dev-1",
        reason="reassign to a dev to fix",
    )

    # Not diverted — proceeds to the normal escalation path.
    release_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Site 4 — reassign / reassign_active_claim diversion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassign_diverts_main_pm_code_to_main_pm_target() -> None:
    svc = _service()
    main_pm_target = uuid4()
    task = _task(team=Team.MAIN_PM, task_type=TaskType.CODE, assigned_to=uuid4())
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))
    _bind(svc, "_is_main_pm_agent", AsyncMock(return_value=True))
    diverted = AsyncMock()
    _bind(svc, "_divert_owned_task_to_pool", diverted)
    # The cell-PM redirect must NOT be reached when the main-pm-code divert fires.
    _bind(
        svc,
        "_resolve_cell_pm_redirect",
        AsyncMock(
            side_effect=AssertionError(
                "cell-PM redirect must not run when main-pm-code divert fires"
            )
        ),
    )

    result = await svc.reassign(task.id, main_pm_target)

    assert result is task
    diverted.assert_awaited_once()


@pytest.mark.asyncio
async def test_reassign_active_claim_diverts_main_pm_code_to_main_pm_target() -> None:
    svc = _service()
    main_pm_target = uuid4()
    task = _task(
        team=Team.MAIN_PM,
        task_type=TaskType.CODE,
        status=TaskStatus.IN_PROGRESS,
        assigned_to=uuid4(),
    )
    _bind(svc, "get", AsyncMock(return_value=task))
    _bind(svc, "_is_board_advisory_agent", AsyncMock(return_value=False))
    _bind(svc, "_is_main_pm_agent", AsyncMock(return_value=True))
    diverted = AsyncMock()
    _bind(svc, "_divert_owned_task_to_pool", diverted)
    _bind(
        svc,
        "_resolve_cell_pm_redirect",
        AsyncMock(
            side_effect=AssertionError(
                "cell-PM redirect must not run when main-pm-code divert fires"
            )
        ),
    )

    result = await svc.reassign_active_claim(task.id, main_pm_target)

    assert result is task
    diverted.assert_awaited_once()


# ---------------------------------------------------------------------------
# Site 5 — claim_task_for_agent: main-pm claiming code (execution state only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_rejects_main_pm_claiming_code_in_execution_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = TaskService.__new__(TaskService)
    svc.session = AsyncMock()
    task = _task(team=Team.BACKEND, task_type=TaskType.CODE, status=TaskStatus.PENDING)
    monkeypatch.setattr(svc, "_load_task_or_raise", AsyncMock(return_value=task))
    plain_claim = AsyncMock(return_value=task)
    monkeypatch.setattr(svc, "claim", plain_claim)

    with pytest.raises(UnauthorizedError, match="MAIN_PM_NO_CODE"):
        await svc.claim_task_for_agent(
            task.id, _agent(role=AgentRole.MAIN_PM), _perms(), claim_target_slug=None
        )

    plain_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_allows_main_pm_recovery_of_code_root_from_needs_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The coordination-recovery path: after a pr_fail / qa_fail / ceo_reject the
    # owning PM re-claims the NEEDS_REVISION root to re-delegate the fixes
    # (lifecycle CLAIM_RULES). A legacy coordination root still typed ``code``
    # (the 2026-06-27 c80e19ff root until the Phase 3b deploy retype) MUST pass
    # through this claim — blocking it wedges the Main PM in needs_revision with
    # no actor and no exit (the loop this bundle closes). The recovery claim
    # re-delegates; it does not execute code.
    svc = TaskService.__new__(TaskService)
    svc.session = AsyncMock()
    task = MagicMock(
        team=Team.MAIN_PM,
        task_type=TaskType.CODE,
        status=TaskStatus.NEEDS_REVISION,
    )
    monkeypatch.setattr(svc, "_load_task_or_raise", AsyncMock(return_value=task))
    claimed = MagicMock()
    plain_claim = AsyncMock(return_value=claimed)
    monkeypatch.setattr(svc, "claim", plain_claim)

    result = await svc.claim_task_for_agent(
        task.id, _agent(role=AgentRole.MAIN_PM), _perms(), claim_target_slug=None
    )

    assert result is claimed
    plain_claim.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_allows_main_pm_claiming_planning_in_execution_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A Main PM coordinates planning roots — claiming a planning task is the
    # legitimate coordination path, not a mismatch.
    svc = TaskService.__new__(TaskService)
    svc.session = AsyncMock()
    task = MagicMock(
        team=Team.MAIN_PM,
        task_type=TaskType.PLANNING,
        status=TaskStatus.PENDING,
    )
    monkeypatch.setattr(svc, "_load_task_or_raise", AsyncMock(return_value=task))
    claimed = MagicMock()
    plain_claim = AsyncMock(return_value=claimed)
    monkeypatch.setattr(svc, "claim", plain_claim)

    result = await svc.claim_task_for_agent(
        task.id, _agent(role=AgentRole.MAIN_PM), _perms(), claim_target_slug=None
    )

    assert result is claimed
    plain_claim.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_does_not_reject_main_pm_code_in_awaiting_pm_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # C8: awaiting_pm_review is a REVIEW state, not an execution state. A
    # Main PM legitimately claims it to complete/merge — the code guard must
    # not fire there (it runs only after the review-state early return).
    svc = TaskService.__new__(TaskService)
    svc.session = AsyncMock()
    task = MagicMock(
        team=Team.MAIN_PM,
        task_type=TaskType.CODE,
        status=TaskStatus.AWAITING_PM_REVIEW,
    )
    monkeypatch.setattr(svc, "_load_task_or_raise", AsyncMock(return_value=task))
    review_claim = AsyncMock(return_value=task)
    monkeypatch.setattr(svc, "_claim_review_state", review_claim)
    plain_claim = AsyncMock(
        side_effect=AssertionError("transitioning claim must not run for review state")
    )
    monkeypatch.setattr(svc, "claim", plain_claim)

    result = await svc.claim_task_for_agent(
        task.id, _agent(role=AgentRole.MAIN_PM), _perms(), claim_target_slug=None
    )

    assert result is task
    review_claim.assert_awaited_once()

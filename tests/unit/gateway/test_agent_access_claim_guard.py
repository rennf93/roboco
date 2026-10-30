"""Project agent-access claim guard (allowed_agents enforcement).

Two layers: the pure predicate ``agent_access_denied_guard`` (pass/deny
shaping only — the deny decision itself lives in
``ProjectService.check_agent_access``) and the Choreographer's
``_agent_access_claim_guard`` wiring (inert without a project dep, without
a task project, or without a usable agent team; fires only on the
work-STARTING opt-in ``check_agent_access=True``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from roboco.config import settings
from roboco.foundation.policy.lifecycle import Role
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.claim_guards import agent_access_denied_guard

# ---------------------------------------------------------------------------
# Pure predicate: agent_access_denied_guard
# ---------------------------------------------------------------------------


def test_predicate_has_access_passes() -> None:
    task = MagicMock(id=uuid4())
    assert agent_access_denied_guard(task, uuid4(), uuid4(), True) is None


def test_predicate_denied_refuses_machine_readably() -> None:
    task = MagicMock(id=uuid4())
    project_id, agent_id = uuid4(), uuid4()
    env = agent_access_denied_guard(task, project_id, agent_id, False)
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert str(project_id) in body["message"]
    assert str(agent_id) in body["message"]
    # The existing write routes stay the only write path named in remediation.
    assert f"/projects/{project_id}/access/{agent_id}" in body["remediate"]


# ---------------------------------------------------------------------------
# Choreographer wiring: _agent_access_claim_guard
# ---------------------------------------------------------------------------


def _make_deps(
    *,
    check_agent_access_result: bool | None = None,
    project: Any = "AUTO",
) -> ChoreographerDeps:
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = None
    deps: dict[str, Any] = {
        "task": task_svc,
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    if project == "AUTO":
        project_svc = AsyncMock()
        if check_agent_access_result is not None:
            project_svc.check_agent_access.return_value = check_agent_access_result
        deps["project"] = project_svc
    elif project is not None:
        deps["project"] = project
    return ChoreographerDeps(**deps)


def _task_with_project(project_id: UUID | None = None) -> MagicMock:
    if project_id is None:
        return MagicMock(id=uuid4(), project=None)
    project = MagicMock(id=project_id)
    return MagicMock(id=uuid4(), project=project)


def _prime_run_claim_guards(deps: ChoreographerDeps, task: MagicMock) -> None:
    """Silence the upstream guards so only the access guard can fire."""
    deps.task.list_in_progress_for_agent.return_value = []
    deps.task.list_paused_for_agent.return_value = []
    deps.task.sequence_hold_reason.return_value = None
    task.dependency_ids = []
    deps.task.has_earlier_incomplete_code_sibling.return_value = False


def _agent_view(agent_id: UUID, team: Any) -> MagicMock:
    return MagicMock(id=agent_id, team=team)


@pytest.mark.asyncio
async def test_rule_denied_refuses_with_not_authorized() -> None:
    project_id = uuid4()
    agent_id = uuid4()
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_view = MagicMock(id=agent_id, team="backend")
    deps.task.agent_for.return_value = agent_view

    env = await c._agent_access_claim_guard(_task_with_project(project_id), agent_id)
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert str(project_id) in body["message"]
    # The services-layer rule made the deny decision, with the resolved team.
    deps.project.check_agent_access.assert_awaited_once_with(
        project_id, agent_id, agent_view.team
    )


@pytest.mark.asyncio
async def test_rule_passes_no_envelope() -> None:
    """allowed_agents=None reaches the rule and passes — no refusal."""
    deps = _make_deps(check_agent_access_result=True)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(id=agent_id, team="backend")

    assert (
        await c._agent_access_claim_guard(_task_with_project(uuid4()), agent_id) is None
    )


@pytest.mark.asyncio
async def test_no_project_dep_is_inert() -> None:
    """Existing ChoreographerDeps constructions (no project) keep the
    guard off — nothing is called."""
    deps = _make_deps(project=None)
    c = Choreographer(deps)
    assert (
        await c._agent_access_claim_guard(_task_with_project(uuid4()), uuid4()) is None
    )
    deps.task.agent_for.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_without_project_is_inert() -> None:
    """A branchless coordination root never reaches the rule."""
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(id=agent_id, team="backend")

    assert (
        await c._agent_access_claim_guard(_task_with_project(project_id=None), agent_id)
        is None
    )
    deps.project.check_agent_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_agent_is_inert() -> None:
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    deps.task.agent_for.return_value = None

    assert (
        await c._agent_access_claim_guard(_task_with_project(uuid4()), uuid4()) is None
    )
    deps.project.check_agent_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_string_team_is_mock_safe() -> None:
    """An unstubbed AsyncMock agent view carries a MagicMock team, not a
    str — the guard stays inert instead of raising (mirrors
    _sequence_claim_guard's mock-safety)."""
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(id=agent_id)  # team auto-mocked

    assert (
        await c._agent_access_claim_guard(_task_with_project(uuid4()), agent_id) is None
    )
    deps.project.check_agent_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_team_value_is_inert() -> None:
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(id=agent_id, team="not-a-cell")

    assert (
        await c._agent_access_claim_guard(_task_with_project(uuid4()), agent_id) is None
    )
    deps.project.check_agent_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_claim_guards_opt_in_false_is_inert() -> None:
    """Default flag off: a denying rule never fires — review/doc/gate
    claims of in-flight work are exempt."""
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(id=agent_id, team="backend")

    task = _task_with_project(uuid4())
    _prime_run_claim_guards(deps, task)
    assert await c._run_claim_guards(agent_id=agent_id, task=task) is None
    deps.project.check_agent_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_claim_guards_opt_in_true_fires_the_guard() -> None:
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(id=agent_id, team="backend")

    task = _task_with_project(uuid4())
    _prime_run_claim_guards(deps, task)
    env = await c._run_claim_guards(
        agent_id=agent_id, task=task, check_agent_access=True
    )
    assert env is not None
    assert env.as_dict()["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_devops_lane_exempt_while_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flag-gated devops exemption at the PROJECT level (mirrors
    TaskService's team-level exemption): a floating devops-1 assigned to a
    cell project's task claims it without an access grant. Found by the
    e2e devops gate arc — the task-level carve-out alone wedged every
    delegation-lane claim behind the assigned-cell rule."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(
        id=agent_id, team="board", role="devops"
    )

    assert (
        await c._agent_access_claim_guard(_task_with_project(uuid4()), agent_id) is None
    )
    deps.project.check_agent_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_devops_off_still_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag off: the devops role gets no exemption — the assigned-cell rule
    applies to it exactly like any other role."""
    monkeypatch.setattr(settings, "devops_enabled", False)
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(
        id=agent_id, team="board", role="devops"
    )

    env = await c._agent_access_claim_guard(_task_with_project(uuid4()), agent_id)
    assert env is not None
    assert env.as_dict()["error"] == "not_authorized"


# ---------------------------------------------------------------------------
# Org-wide exemption: coordination/board roles act across cells by design
# (lifecycle._ORG_WIDE_ROLES minus the flag-gated devops role). Before this
# exemption the assigned-cell check wedged every main-pm root claim on a
# cell-assigned project (live 2026-09-27).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    ["main_pm", "product_owner", "head_marketing", "auditor", "pr_reviewer", "ceo"],
)
async def test_org_wide_roles_exempt_from_assigned_cell_rule(role: str) -> None:
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(id=agent_id, team="main_pm", role=role)

    assert (
        await c._agent_access_claim_guard(_task_with_project(uuid4()), agent_id) is None
    )
    deps.project.check_agent_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_org_wide_exempt_accepts_role_enum() -> None:
    """agent_for rows carry a Role enum in production — the .value path
    must exempt the same way the plain-string path does."""
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(
        id=agent_id, team="main_pm", role=Role.MAIN_PM
    )

    assert (
        await c._agent_access_claim_guard(_task_with_project(uuid4()), agent_id) is None
    )
    deps.project.check_agent_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_cell_role_still_refused_on_other_cells_project() -> None:
    """The exemption is role-scoped: a cell agent on a foreign cell's
    project is still refused — the org-wide carve-out must not become a
    blanket bypass."""
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(
        id=agent_id, team="frontend", role="developer"
    )

    env = await c._agent_access_claim_guard(_task_with_project(uuid4()), agent_id)
    assert env is not None
    assert env.as_dict()["error"] == "not_authorized"


# ---------------------------------------------------------------------------
# Reason-specific refusal (F3): the deny reason picks the remediate, so the
# envelope never sends an agent down a path that cannot work.
# ---------------------------------------------------------------------------


def test_predicate_assigned_cell_reason_names_reassignment_only() -> None:
    """A cross-cell refusal cannot be fixed by the access route (the cell
    check precedes the list check), so the remediate must not send the
    agent there."""
    task = MagicMock(id=uuid4())
    project_id, agent_id = uuid4(), uuid4()
    env = agent_access_denied_guard(
        task, project_id, agent_id, False, reason="assigned_cell"
    )
    assert env is not None
    body = env.as_dict()
    assert "outside the project's assigned cell" in body["message"]
    # The route is named only to be negated: it cannot fix a cross-cell
    # refusal, and the remediate must say so instead of suggesting it.
    assert "cannot widen" in body["remediate"]
    assert "reassign" in body["remediate"].lower()


@pytest.mark.asyncio
async def test_same_cell_exclusion_remediate_names_the_access_route() -> None:
    """A same-cell agent left off the allowed list CAN be fixed by the
    access route — the remediate keeps pointing at it."""
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(
        id=agent_id, team="backend", role="developer"
    )
    task = _task_with_project(uuid4())
    task.project.assigned_cell = "backend"

    env = await c._agent_access_claim_guard(task, agent_id)
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert "access/" in body["remediate"]


@pytest.mark.asyncio
async def test_cross_cell_refusal_remediate_names_reassignment() -> None:
    """End to end: a cell agent on a foreign cell's project produces the
    reassign remediate, never the access route as a fix (the main-pm wedge
    showed the coordinator shape of this refusal live)."""
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(
        id=agent_id, team="frontend", role="developer"
    )
    task = _task_with_project(uuid4())
    task.project.assigned_cell = "backend"

    env = await c._agent_access_claim_guard(task, agent_id)
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert "cannot widen" in body["remediate"]
    assert "reassign" in body["remediate"].lower()


# ---------------------------------------------------------------------------
# give_me_work / pm_give_me_work access pre-filter (F7): the offer half of
# the offer-then-reject loop — an agent excluded from a project is never
# offered its tasks, instead of offered-then-refused until the circuit
# breaker trips.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drop_access_denied_filters_refused_claimables_only() -> None:
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(
        id=agent_id, team="backend", role="developer"
    )
    denied_pending = MagicMock(status="pending", project=MagicMock(id=uuid4()))
    denied_revision = MagicMock(status="needs_revision", project=MagicMock(id=uuid4()))
    held_in_progress = MagicMock(status="in_progress", project=MagicMock(id=uuid4()))
    branchless = MagicMock(status="pending", project=None)

    out = await c._drop_access_denied(
        [denied_pending, denied_revision, held_in_progress, branchless], agent_id
    )
    assert out == [held_in_progress, branchless]


@pytest.mark.asyncio
async def test_drop_access_denied_keeps_allowed_agent_work() -> None:
    deps = _make_deps(check_agent_access_result=True)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(
        id=agent_id, team="backend", role="developer"
    )
    offered = MagicMock(status="pending", project=MagicMock(id=uuid4()))

    assert await c._drop_access_denied([offered], agent_id) == [offered]


@pytest.mark.asyncio
async def test_drop_access_denied_keeps_org_wide_actors_work() -> None:
    """The pre-filter must exempt exactly like the claim guard, or it would
    hide main-pm's cross-cell roots from give_me_work — a NEW wedge replacing
    the old one."""
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(
        id=agent_id, team="main_pm", role="main_pm"
    )
    root = MagicMock(status="pending", project=MagicMock(id=uuid4()))

    assert await c._drop_access_denied([root], agent_id) == [root]


@pytest.mark.asyncio
async def test_drop_access_denied_inert_without_project_dep() -> None:
    deps = _make_deps(project=None)
    c = Choreographer(deps)
    tasks = [MagicMock(status="pending")]

    assert await c._drop_access_denied(tasks, uuid4()) == tasks
    deps.task.agent_for.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_assignee_passes_cross_cell_rule() -> None:
    """Regression (fe-pm, 2026-09-27): the coordinator's explicit assignment
    IS authorization. fe slices of the backend-assigned roboco-api project
    were refused at i_will_plan four times — not_authorized on the assigned
    cell — so fe-pm escalated five times, blocked all three roots itself,
    and the frontend cell starved. The task's own assignee must pass the
    access verdict without touching the rule (the POST /access grant surface
    stays unable to widen an assigned cell)."""
    project_id = uuid4()
    agent_id = uuid4()
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    deps.task.agent_for.return_value = MagicMock(id=agent_id, team="frontend")
    task = _task_with_project(project_id)
    task.assigned_to = agent_id

    assert await c._agent_access_claim_guard(task, agent_id) is None
    deps.project.check_agent_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_drop_access_denied_keeps_explicitly_assigned_cross_cell_task() -> None:
    """The give_me_work offer pre-filter shares the access verdict, so the
    assignee passthrough must keep an assigned cross-cell task offerable —
    the offer-then-reject class must not reappear one layer down."""
    deps = _make_deps(check_agent_access_result=False)
    c = Choreographer(deps)
    agent_id = uuid4()
    deps.task.agent_for.return_value = MagicMock(id=agent_id, team="frontend")
    offered = MagicMock(
        status="pending", project=MagicMock(id=uuid4()), assigned_to=agent_id
    )

    assert await c._drop_access_denied([offered], agent_id) == [offered]
    deps.project.check_agent_access.assert_not_awaited()

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

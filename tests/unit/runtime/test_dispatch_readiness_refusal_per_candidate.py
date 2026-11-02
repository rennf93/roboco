"""A spawn refused as not ready ends one candidate's dispatch, never the loop.

``spawn_agent`` refuses a task that is not ready by auto-blocking it and raising
``AgentReadinessError`` ("callers should log and move on"). Every per-candidate
dispatcher loop let it escape: ``_dispatch_all_work`` logged "Dispatcher raised;
continuing with next dispatcher" and every candidate after the refused one
waited a tick (live 2026-10-28 06:40:08 for pm_work, fe-pm on d42e53ea). Each
scenario below gives a dispatcher two candidates and refuses the first one's
spawn: the dispatcher must not raise and must still dispatch the second.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator, AgentReadinessError
from roboco.seeds.initial_data import AGENT_UUIDS

_FIRST = "11111111-1111-4111-8111-111111111111"
_SECOND = "22222222-2222-4222-8222-222222222222"
_REFUSAL = "spawn refused (test): Task waiting on non-terminal dependency"

Scenario = Callable[[AgentOrchestrator, pytest.MonkeyPatch], Awaitable[list[str]]]


def _orchestrator(monkeypatch: pytest.MonkeyPatch) -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._tick_handled_tasks = set()
    monkeypatch.setattr(orch, "_pm_respawn_should_gate", AsyncMock(return_value=False))
    monkeypatch.setattr(orch, "_task_git_context", lambda _task: None)
    return orch


def _refusing_spawn(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    """``spawn_agent`` refusing the first candidate; returns the ids it was
    asked to spawn, in order (notification spawns carry the agent instead)."""
    asked: list[str] = []

    async def _spawn(*, task_id: str | None = None, **kw: Any) -> None:
        asked.append(task_id or kw["initial_prompt"])
        if task_id == _FIRST or kw.get("initial_prompt") == _FIRST:
            raise AgentReadinessError(_REFUSAL)

    monkeypatch.setattr(orch, "spawn_agent", AsyncMock(side_effect=_spawn))
    return asked


def _refusing_step(
    name: str, orch: AgentOrchestrator, mp: pytest.MonkeyPatch
) -> list[str]:
    """A per-candidate step ``name`` whose spawn is refused for the first task."""
    asked: list[str] = []

    async def _step(*args: Any) -> None:
        task_id = next(str(a["id"]) for a in args if isinstance(a, dict))
        asked.append(task_id)
        if task_id == _FIRST:
            raise AgentReadinessError(_REFUSAL)

    mp.setattr(orch, name, _step)
    return asked


def _tasks(**fields: Any) -> list[dict[str, Any]]:
    return [{"id": _FIRST, **fields}, {"id": _SECOND, **fields}]


async def _revision_coordination(
    orch: AgentOrchestrator, mp: pytest.MonkeyPatch
) -> list[str]:
    asked = _refusing_spawn(orch, mp)
    tasks = _tasks(status="needs_revision", assigned_to=AGENT_UUIDS["be-pm"])
    mp.setattr(orch, "_fetch_tasks", AsyncMock(return_value=tasks))
    mp.setattr(orch, "_get_prompt_for_agent", AsyncMock(return_value="p"))
    await orch._dispatch_revision_coordination_roots(MagicMock())
    return asked


async def _pm_review(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_spawn(orch, mp)
    mp.setattr(orch, "_fetch_tasks", AsyncMock(return_value=_tasks(team="backend")))
    mp.setattr(orch, "_blocked_by_earlier_sibling", AsyncMock(return_value=False))
    mp.setattr(orch, "_review_pm_slug", AsyncMock(return_value="be-pm"))
    mp.setattr(orch, "_build_pm_review_prompt", lambda _t: "p")
    await orch._dispatch_pm_review_work(MagicMock())
    return asked


async def _blocker(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_spawn(orch, mp)
    mp.setattr(orch, "_fetch_tasks", AsyncMock(return_value=_tasks(status="blocked")))
    mp.setattr(orch, "_check_dependencies_terminal", AsyncMock(return_value=None))
    mp.setattr(orch, "_blocker_resolver_slug", lambda _t: "be-pm")
    mp.setattr(orch, "_build_pm_blocker_prompt", lambda _t: "p")
    await orch._dispatch_blocker_work(MagicMock())
    return asked


async def _marketing(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_spawn(orch, mp)
    mp.setattr(orch, "_fetch_tasks", AsyncMock(return_value=_tasks(team="marketing")))
    mp.setattr(orch, "_build_marketing_prompt", lambda _t: "p")
    await orch._dispatch_marketing_work(MagicMock())
    return asked


async def _dev_work(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_step("_dev_dispatch_one", orch, mp)
    mp.setattr(orch, "_fetch_tasks", AsyncMock(return_value=_tasks(source="manual")))
    await orch._dispatch_dev_work(MagicMock())
    return asked


async def _claimed_without_agent(
    orch: AgentOrchestrator, mp: pytest.MonkeyPatch
) -> list[str]:
    asked = _refusing_spawn(orch, mp)
    mp.setattr(orch, "_fetch_tasks", AsyncMock(return_value=_tasks(status="claimed")))
    mp.setattr(orch, "_claimed_task_needs_agent", lambda _t: "main-pm")
    mp.setattr(orch, "_get_prompt_for_agent", AsyncMock(return_value="p"))
    await orch._dispatch_claimed_without_agent(MagicMock())
    return asked


async def _qa_unassigned(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_spawn(orch, mp)
    tasks = _tasks(status="awaiting_qa", team="backend")
    mp.setattr(orch, "_fetch_tasks", AsyncMock(return_value=tasks))
    mp.setattr(orch, "_decisions_order_qa_queue", AsyncMock(return_value=(tasks, {})))
    mp.setattr(orch, "_select_agent_for_cell", lambda _team, _role: "be-qa")
    mp.setattr(orch, "_decisions_qa_depth_directive", lambda _t, _d: "")
    mp.setattr(orch, "_build_qa_prompt", lambda _t: "p")
    await orch._dispatch_qa_work(MagicMock())
    return asked


async def _qa_assigned(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_spawn(orch, mp)
    tasks = _tasks(
        status="awaiting_qa", team="backend", assigned_to=AGENT_UUIDS["be-qa"]
    )
    mp.setattr(orch, "_fetch_tasks", AsyncMock(return_value=tasks))
    mp.setattr(orch, "_decisions_order_qa_queue", AsyncMock(return_value=(tasks, {})))
    mp.setattr(orch, "_build_qa_prompt", lambda _t: "p")
    await orch._dispatch_qa_work(MagicMock())
    return asked


async def _doc_work(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_step("_doc_dispatch_one", orch, mp)
    mp.setattr(orch, "_fetch_tasks", AsyncMock(return_value=_tasks()))
    await orch._dispatch_doc_work(MagicMock())
    return asked


async def _pr_review(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_spawn(orch, mp)
    mp.setattr(
        orch, "_fetch_tasks", AsyncMock(return_value=_tasks(source="external_pr"))
    )
    mp.setattr(orch, "_build_pr_review_prompt", lambda _t: "p")
    await orch._dispatch_pr_review_work(MagicMock())
    return asked


async def _pr_gate(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    """One reviewer for both gate tasks: a refused spawn must not use up the
    reviewer's one task per tick."""
    asked = _refusing_spawn(orch, mp)
    mp.setattr(orch, "_fetch_tasks", AsyncMock(return_value=_tasks(team="backend")))
    mp.setattr(orch, "_gate_task_ci_pending", AsyncMock(return_value=False))
    mp.setattr(orch, "_gate_task_reviewer", lambda _t: "be-pr-reviewer")
    mp.setattr(orch, "_sync_gate_reviewer_assignment", AsyncMock())
    mp.setattr(orch, "_build_pr_gate_prompt", lambda _t: "p")
    mp.setattr(orch, "_dispatch_devops_gate_review", AsyncMock())
    await orch._dispatch_pr_gate_work(MagicMock())
    return asked


async def _devops_gate(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_step("_dispatch_devops_gate_review", orch, mp)
    mp.setattr(orch, "_fetch_tasks", AsyncMock(return_value=_tasks(team="backend")))
    mp.setattr(orch, "_gate_task_ci_pending", AsyncMock(return_value=True))
    await orch._dispatch_pr_gate_work(MagicMock())
    return asked


async def _pm_closure(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_step("_maybe_spawn_pm_closure", orch, mp)

    async def _fetch(_client: Any, status: str, **_kw: Any) -> list[dict[str, Any]]:
        return _tasks(status=status) if status == "claimed" else []

    mp.setattr(orch, "_fetch_tasks", _fetch)
    await orch._dispatch_pm_closure_work(MagicMock())
    return asked


def _notifications(slug: str) -> list[dict[str, Any]]:
    return [
        {"id": _FIRST, "to_agents": [AGENT_UUIDS[slug]]},
        {"id": _SECOND, "to_agents": [AGENT_UUIDS[slug]]},
    ]


async def _escalation(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_spawn(orch, mp)
    mp.setattr(
        orch, "_fetch_notifications", AsyncMock(return_value=_notifications("be-pm"))
    )
    mp.setattr(orch, "_notification_spawn_cooled", AsyncMock(return_value=False))
    mp.setattr(orch, "_notification_has_live_work", AsyncMock(return_value=True))
    mp.setattr(orch, "_build_escalation_prompt", lambda n: n["id"])
    await orch._dispatch_escalation_work(MagicMock())
    return asked


async def _approval(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_spawn(orch, mp)
    mp.setattr(
        orch, "_fetch_notifications", AsyncMock(return_value=_notifications("main-pm"))
    )
    mp.setattr(orch, "_notification_spawn_cooled", AsyncMock(return_value=False))
    mp.setattr(orch, "_notification_has_live_work", AsyncMock(return_value=True))
    mp.setattr(orch, "_build_approval_prompt", lambda n: n["id"])
    await orch._dispatch_approval_work(MagicMock())
    return asked


async def _a2a(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked = _refusing_spawn(orch, mp)
    mp.setattr(
        orch, "_fetch_notifications", AsyncMock(return_value=_notifications("be-dev-1"))
    )
    mp.setattr(orch, "_notification_spawn_cooled", AsyncMock(return_value=False))
    mp.setattr(orch, "_build_a2a_prompt", lambda n: n["id"])
    await orch._dispatch_a2a_work(MagicMock())
    return asked


async def _vault_curation(orch: AgentOrchestrator, mp: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    async def _curate(task_id: str, _title: str) -> None:
        asked.append(task_id)
        if task_id == _FIRST:
            raise AgentReadinessError(_REFUSAL)

    mp.setattr(orch, "_maybe_spawn_vault_curation", _curate)
    mp.setattr("roboco.config.settings.obsidian_vault_enabled", True)
    roots = [MagicMock(id=_FIRST, title="a"), MagicMock(id=_SECOND, title="b")]
    db_ctx = MagicMock()
    db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    db_ctx.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("roboco.db.base.get_db_context", return_value=db_ctx),
        patch(
            "roboco.services.task.TaskService.list_completed_roots_pending_vault_curation",
            AsyncMock(return_value=roots),
        ),
    ):
        await orch._dispatch_vault_curation_work(MagicMock())
    return asked


@pytest.mark.parametrize(
    "scenario",
    [
        _revision_coordination,
        _pm_review,
        _blocker,
        _marketing,
        _dev_work,
        _claimed_without_agent,
        _qa_unassigned,
        _qa_assigned,
        _doc_work,
        _pr_review,
        _pr_gate,
        _devops_gate,
        _pm_closure,
        _escalation,
        _approval,
        _a2a,
        _vault_curation,
    ],
    ids=lambda s: s.__name__.strip("_"),
)
@pytest.mark.asyncio
async def test_a_refused_candidate_does_not_stop_the_next_one(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(monkeypatch)

    asked = await scenario(orch, monkeypatch)

    assert asked == [_FIRST, _SECOND]


@pytest.mark.asyncio
async def test_a_refused_resume_spawn_leaves_the_author_held_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dev twin of the closure auto-resume spawns inside the held-work
    check; its refusal must not escape the dev or agentless-claim loop."""
    orch = _orchestrator(monkeypatch)
    _refusing_spawn(orch, monkeypatch)
    paused = {"id": _FIRST, "status": "paused"}
    monkeypatch.setattr(orch, "_fetch_task_fields", AsyncMock(return_value=paused))
    monkeypatch.setattr(orch, "_build_dev_prompt", AsyncMock(return_value="p"))
    monkeypatch.setattr(type(orch), "_api_url", property(lambda _self: "http://api"))
    client = MagicMock()
    client.patch = AsyncMock(return_value=MagicMock(status_code=200))

    await orch._resume_paused_task_for_author(client, _FIRST, "be-dev-1")

    orch_any: Any = orch
    orch_any.spawn_agent.assert_awaited_once()

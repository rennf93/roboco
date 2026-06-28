"""F059: self-heal fix tasks must WAIT for the CEO's Approve-&-Start.

The module docstring promises the loop 'only NOTIFIES and, at most, OPENS a
PENDING task ... the task waits for the CEO's Approve-&-Start and terminates at
awaiting_ceo_approval'. The implementation did the opposite: it created the
task ``confirmed_by_human=True`` and the orchestrator dispatched it at once —
a self-heal fix that re-broke CI would trigger another self-heal cycle, open
another auto-dispatched fix, and loop with no CEO gate on dispatch.

The fix restores the documented gate:
* ``_originate`` opens the task ``confirmed_by_human=False`` (held for the CEO).
* The orchestrator holds a self-heal task out of dispatch until the CEO
  approves it (``confirmed_by_human`` flips True via ``approve_and_start``).
* ``give_me_work`` (``list_pending_for_agent``) never offers a held task to an
  already-alive agent.
* ``approve_and_start`` is the CEO's start gate — it flips ``confirmed_by_human``
  True so the held task finally dispatches.

The 'never self-deploys' guarantee (no merge) is unchanged; only the dispatch
gate is restored.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import roboco.services.self_heal_engine as _self_heal_mod
from roboco.foundation import identity as _foundation
from roboco.models.base import TaskStatus, TaskType
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.self_heal_engine import RegressionObservation, SelfHealEngine
from roboco.services.task import (
    SELF_HEAL_SOURCE,
    TaskCreateRequest,
    TaskService,
)
from sqlalchemy.dialects import postgresql

MAIN_PM_UUID = _foundation.AGENTS["main-pm"].uuid
SYSTEM_UUID = _foundation.AGENTS["system"].uuid


def _task(
    tid: str,
    source: str,
    *,
    assigned_to: str | None = None,
    confirmed: bool = True,
) -> dict[str, Any]:
    return {
        "id": tid,
        "source": source,
        "assigned_to": assigned_to,
        "confirmed_by_human": confirmed,
    }


# ---------------------------------------------------------------------------
# Orchestrator: a held self-heal task (not yet CEO-confirmed) is NOT dispatched
# ---------------------------------------------------------------------------


def _pm_stub(tasks: list[dict[str, Any]]) -> MagicMock:
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="main-pm")
    stub._BOARD_AGENTS = frozenset()
    stub._route_unassigned_pm_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()
    return stub


@pytest.mark.asyncio
async def test_held_self_heal_task_is_not_dispatched() -> None:
    """A self-heal fix task the CEO has NOT yet approved (confirmed_by_human=False)
    is held out of the assigned-PM dispatch path — no autonomous dispatch."""
    tasks = [
        _task("A", SELF_HEAL_SOURCE, assigned_to="main-pm", confirmed=False),
    ]
    stub = _pm_stub(tasks)
    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._handle_pm_assigned_task.assert_not_awaited()
    stub._route_unassigned_pm_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_ceo_approved_self_heal_task_dispatches() -> None:
    """Once the CEO has approved the self-heal task (confirmed_by_human=True via
    approve_and_start), it dispatches through the assigned-PM path normally."""
    tasks = [
        _task("A", SELF_HEAL_SOURCE, assigned_to="main-pm", confirmed=True),
        _task("B", SELF_HEAL_SOURCE, assigned_to="main-pm", confirmed=False),
    ]
    stub = _pm_stub(tasks)
    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    handled = [c.args[0]["id"] for c in stub._handle_pm_assigned_task.await_args_list]
    assert handled == ["A"]  # only the CEO-approved one


@pytest.mark.asyncio
async def test_non_self_heal_tasks_dispatch_regardless_of_confirmation() -> None:
    """The hold is specific to self-heal — an ordinary confirmed-by-human task
    is NOT gated by this skip (regression guard for the board/intake flow)."""
    tasks = [
        _task("A", "manual", assigned_to="main-pm", confirmed=False),
        _task("B", "manual", assigned_to="main-pm", confirmed=True),
    ]
    stub = _pm_stub(tasks)
    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    handled = [c.args[0]["id"] for c in stub._handle_pm_assigned_task.await_args_list]
    assert set(handled) == {"A", "B"}


# ---------------------------------------------------------------------------
# _originate: the opened task is held for the CEO (confirmed_by_human=False)
# ---------------------------------------------------------------------------


def _originate_engine(captured: dict[str, Any]) -> SelfHealEngine:
    """An engine whose task/project services are mocks that capture the create
    request — so the held-for-CEO invariant is unit-testable without a DB."""
    session = MagicMock()

    task_svc = MagicMock()
    task_svc.list_open_self_heal_tasks = AsyncMock(return_value=[])
    task_svc.create = AsyncMock(
        side_effect=lambda req: (captured.setdefault("req", req), MagicMock(id="t1"))[1]
    )

    project_svc = MagicMock()
    project_svc.get_by_slug = AsyncMock(return_value=MagicMock(id="p1"))

    engine = SelfHealEngine.__new__(SelfHealEngine)  # skip __init__ (no source)
    engine.session = session
    engine.log = MagicMock()
    engine._source = MagicMock()
    return engine, task_svc, project_svc, _self_heal_mod


@pytest.mark.asyncio
async def test_originate_opens_task_held_for_ceo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The opened self-heal fix task is ``confirmed_by_human=False`` — held for
    the CEO's Approve-&-Start, NOT auto-confirmed for autonomous dispatch."""
    captured: dict[str, Any] = {}
    engine, task_svc, project_svc, mod = _originate_engine(captured)
    monkeypatch.setattr(mod, "get_task_service", lambda _s: task_svc)
    monkeypatch.setattr(mod, "get_project_service", lambda _s: project_svc)
    monkeypatch.setattr(mod, "markers", MagicMock())  # set_self_heal_fingerprint no-op

    obs = [
        RegressionObservation(
            fingerprint="fp1",
            signal_name="ci:roboco",
            repo_hint="roboco",
            summary="x",
            detail="d",
            raw_ref="r",
        )
    ]
    # Bypass the session.flush (MagicMock session) — _originate awaits it.
    engine.session.flush = AsyncMock()  # type: ignore[assignment]

    count = await engine._originate(obs)

    assert count == 1
    req: TaskCreateRequest = captured["req"]
    assert req.confirmed_by_human is False  # held for the CEO — the F059 fix
    assert req.status == TaskStatus.PENDING
    assert req.source == SELF_HEAL_SOURCE
    assert req.task_type == TaskType.CODE


# ---------------------------------------------------------------------------
# approve_and_start: the CEO's start gate lifts the hold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_and_start_lifts_the_ceo_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``approve_and_start`` is the CEO's start gate — it flips
    ``confirmed_by_human`` True so a held self-heal task finally dispatches."""
    svc = TaskService(MagicMock())
    svc.session.flush = AsyncMock()  # type: ignore[assignment]
    task = MagicMock()
    task.status = TaskStatus.PENDING
    task.team = MagicMock()
    task.team.value = "main_pm"
    task.board_review_complete = True
    task.confirmed_by_human = False  # held self-heal task
    task.assigned_to = "someone-else"
    task.task_type = MagicMock()
    task.task_type.value = "code"
    svc.get = AsyncMock(return_value=task)
    main_pm = MagicMock(id=MAIN_PM_UUID)
    agent_svc = MagicMock()
    agent_svc.get_by_slug = AsyncMock(return_value=main_pm)
    monkeypatch.setattr(
        "roboco.services.agent.get_agent_service",
        MagicMock(return_value=agent_svc),
    )
    monkeypatch.setattr(svc, "_activate_batch_root_subtasks", AsyncMock())
    monkeypatch.setattr(svc, "_emit_task_event", AsyncMock())
    monkeypatch.setattr(
        "roboco.services.task.main_pm_cannot_own_code", lambda *_args, **_kwargs: False
    )

    await svc.approve_and_start(_foundation.AGENTS["main-pm"].uuid, notes=None)

    assert task.confirmed_by_human is True  # the hold lifts on CEO approval


# ---------------------------------------------------------------------------
# list_pending_for_agent: give_me_work never offers a held task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_for_agent_excludes_held_self_heal() -> None:
    """A held self-heal fix task is never offered via give_me_work — an
    already-alive PM can't grab it before the CEO approves.

    Asserted at the SQL layer: the query scopes the hold to self-heal
    (``source != 'self_heal' OR confirmed_by_human``), so the database drops a
    held self-heal task before the agent ever sees the list.
    """
    svc = TaskService(MagicMock())
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    svc.session.execute = AsyncMock(return_value=result)
    svc.unmet_dependency_ids = AsyncMock(return_value=[])  # type: ignore[assignment]

    await svc.list_pending_for_agent(MAIN_PM_UUID)

    stmt = svc.session.execute.await_args.args[0]
    compiled = str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "self_heal" in compiled  # scoped to self-heal, not a universal gate
    assert "confirmed_by_human" in compiled


@pytest.mark.asyncio
async def test_list_pending_for_agent_still_offers_delegated_subtask() -> None:
    """Regression guard (F059): the hold is scoped to self-heal. A delegated
    subtask (source != self_heal, confirmed_by_human=False — the default for
    PM-delegated work, where the delegation IS the authorization to start) must
    STILL be offered via give_me_work. A universal confirmed_by_human filter
    would starve devs of all delegated work."""
    svc = TaskService(MagicMock())

    delegated = MagicMock()
    delegated.source = "manual"  # not self_heal
    delegated.confirmed_by_human = False  # the delegation default
    delegated.dependency_ids = []

    result = MagicMock()
    result.scalars.return_value.all.return_value = [delegated]
    svc.session.execute = AsyncMock(return_value=result)
    svc.unmet_dependency_ids = AsyncMock(return_value=[])  # type: ignore[assignment]

    available = await svc.list_pending_for_agent(MAIN_PM_UUID)

    assert delegated in available


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

"""Coroner postmortem dispatch — Auditor-solo, EVENT-triggered, never the
two-reviewer board-review gate, never the dev/PM delivery dispatchers.
Mirrors test_pest_control_dispatch.py, minus the "already authored" marker
pre-check (propose_postmortem completes the task atomically, like the
feature-spotlight dispatch — see test_roadmap_dispatch.py's sibling instead
for that shape difference)."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import CORONER_SOURCE


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    orch._instances = {}
    orch._board_dispatched = set()
    return orch


def _coroner_task(
    *, orchestration_markers: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "title": "Coroner postmortem",
        "description": "Autopsy an incident and propose one process change.",
        "assigned_to": "auditor",
        "source": CORONER_SOURCE,
        "orchestration_markers": orchestration_markers
        or {
            "coroner_incident": {
                "incident_task_id": str(uuid4()),
                "kind": "bounced",
                "revision_count": 3,
                "title": "Chronic task",
            }
        },
    }


@pytest.mark.asyncio
async def test_coroner_dispatch_spawns_only_auditor() -> None:
    orch = _make_orch()
    task = _coroner_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "_coroner_incident_context", AsyncMock(return_value="")),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_coroner_exploration(task)

    spawn.assert_awaited_once()
    calls = list(spawn.await_args_list)
    assert calls[0].kwargs["agent_id"] == "auditor"
    assert calls[0].kwargs["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_coroner_dispatch_retries_until_breaker() -> None:
    """A failed exploration must be retried, not abandoned.

    The explorer has a progression verb (``propose_*``), so a respawn CAN
    advance the task — unlike the two-reviewer review pass this guard was
    originally written for. Bounding belongs to
    ``_pm_respawn_should_gate`` (DB-persisted, reset by a status change),
    not to a never-expiring in-memory set.
    """
    orch = _make_orch()
    task = _coroner_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "_coroner_incident_context", AsyncMock(return_value="")),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_coroner_exploration(task)
        await orch._dispatch_coroner_exploration(task)

    ticks = 2
    assert spawn.await_count == ticks, (
        "a second tick must re-attempt a failed exploration"
    )


@pytest.mark.asyncio
async def test_coroner_dispatch_skips_active_auditor() -> None:
    orch = _make_orch()
    task = _coroner_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=True),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_coroner_exploration(task)

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pm_work_routes_coroner_away_from_board_handler() -> None:
    """A board_coroner task must ride the dedicated dispatcher, never the
    two-reviewer ``_handle_board_assigned_task`` (auditor isn't even in
    _BOARD_AGENTS, so this also guards against falling into the generic
    PM-assigned handler)."""
    task = _coroner_task()
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=[task])
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="auditor")
    stub._BOARD_AGENTS = frozenset({"product-owner", "head-marketing"})
    stub._dispatch_coroner_exploration = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._route_unassigned_pm_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._dispatch_coroner_exploration.assert_awaited_once()
    stub._handle_board_assigned_task.assert_not_awaited()
    stub._handle_pm_assigned_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_coroner_tasks_are_never_routed_by_dev_dispatch() -> None:
    tasks = [_coroner_task()]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._dev_dispatch_one = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_dev_work(cast("AgentOrchestrator", stub), client)

    stub._dev_dispatch_one.assert_not_awaited()


def test_coroner_prompt_names_solo_auditor_and_real_verbs() -> None:
    orch = _make_orch()
    task = _coroner_task()
    prompt = orch._build_coroner_prompt(task)
    assert "triage()" in prompt
    assert "evidence(" in prompt
    assert "propose_postmortem(" in prompt
    assert "i_am_idle()" in prompt
    incident = task["orchestration_markers"]["coroner_incident"]
    assert incident["incident_task_id"] in prompt
    assert "bounced" in prompt


def test_coroner_prompt_omits_evidence_section_when_empty() -> None:
    orch = _make_orch()
    prompt = orch._build_coroner_prompt(_coroner_task())
    assert "## Evidence gathered for you" not in prompt


def test_coroner_prompt_renders_evidence_when_given() -> None:
    orch = _make_orch()
    prompt = orch._build_coroner_prompt(
        _coroner_task(), "Findings ledger:\n- [blocker] round 1: file.py:10 — AC 1"
    )
    assert "## Evidence gathered for you" in prompt
    assert "file.py:10" in prompt


@pytest.mark.asyncio
async def test_coroner_dispatch_injects_incident_context() -> None:
    orch = _make_orch()
    task = _coroner_task()
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(
            orch,
            "_coroner_incident_context",
            AsyncMock(return_value="- [blocker] round 1: file.py:10 — AC 1"),
        ),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_coroner_exploration(task)

    prompt = spawn.await_args_list[0].kwargs["initial_prompt"]
    assert "file.py:10" in prompt


@pytest.mark.asyncio
async def test_coroner_incident_context_survives_db_failure() -> None:
    """A DB hiccup gathering incident context degrades to '' — never raises."""
    orch = _make_orch()
    task = _coroner_task()
    with patch(
        "roboco.services.coroner_engine.get_coroner_engine",
        side_effect=RuntimeError("db down"),
    ):
        result = await orch._coroner_incident_context(task)
    assert result == ""


@pytest.mark.asyncio
async def test_coroner_incident_context_empty_without_marker() -> None:
    orch = _make_orch()
    task = _coroner_task(orchestration_markers={})
    result = await orch._coroner_incident_context(task)
    assert result == ""

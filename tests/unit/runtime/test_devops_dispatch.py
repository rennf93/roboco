"""Stage-1 devops spawn lane: flag-gated spawning through the generic
assigned-work dev dispatcher, plus the readiness-gate widening for the
devops-owned rework states.

Flag off => byte-for-byte today: a pending task assigned to devops-1 is
skipped by `_dev_dispatch_one`'s role guard (devops-1 is seeded but nothing
spawns it). Flag on => the same generic path spawns devops-1 exactly like a
developer: pending -> `_spawn_pending_dev`, rework -> `_handle_dev_existing_owner`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    import httpx


def _orch() -> AgentOrchestrator:
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


def _client() -> httpx.AsyncClient:
    return cast("httpx.AsyncClient", object())


def _assigned_devops_task(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "status": "pending",
        "team": "backend",
        "task_type": "code",
        "title": "Harden the compose topology",
        "assigned_to": str(uuid4()),
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_flag_off_devops_assignment_never_spawns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag off: the devops role guard returns early (no spawn, no churn):
    the pre-Stage-1 behavior, byte for byte."""
    monkeypatch.setattr(settings, "devops_enabled", False)
    orch = _orch()
    task = _assigned_devops_task()
    with (
        patch.object(orch, "_resolve_agent_slug", return_value="devops-1"),
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_spawn_pending_dev", new=AsyncMock()) as pending,
        patch.object(orch, "_handle_dev_existing_owner", new=AsyncMock()) as existing,
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dev_dispatch_one(_client(), task)

    spawn.assert_not_awaited()
    pending.assert_not_awaited()
    existing.assert_not_awaited()


@pytest.mark.asyncio
async def test_flag_on_assigned_pending_devops_task_spawns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on: a pending task pre-assigned to devops-1 rides the SAME
    generic spawn path as a developer's pre-assigned leaf."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    orch = _orch()
    client = _client()
    task = _assigned_devops_task()
    with (
        patch.object(orch, "_resolve_agent_slug", return_value="devops-1"),
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_dev_dispatch_role_matches", return_value=True) as matches,
        patch.object(orch, "_spawn_pending_dev", new=AsyncMock()) as pending,
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dev_dispatch_one(client, task)

    spawn.assert_not_awaited()  # spawn happens inside _spawn_pending_dev
    pending.assert_awaited_once_with(client, task, "devops-1")
    matches.assert_called_once()


@pytest.mark.asyncio
async def test_flag_on_devops_needs_revision_respawns_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on: a needs_revision task owned by devops-1 takes the
    existing-owner branch (rework respawn), not the pending branch."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    orch = _orch()
    task = _assigned_devops_task(status="needs_revision")
    with (
        patch.object(orch, "_resolve_agent_slug", return_value="devops-1"),
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_dev_dispatch_role_matches", return_value=True),
        patch.object(orch, "_handle_dev_existing_owner", new=AsyncMock()) as existing,
        patch.object(orch, "_spawn_pending_dev", new=AsyncMock()) as pending,
    ):
        await orch._dev_dispatch_one(_client(), task)

    existing.assert_awaited_once_with(task, "needs_revision", "devops-1")
    pending.assert_not_awaited()


@pytest.mark.asyncio
async def test_flag_on_doc_typed_devops_assignment_is_a_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The role/task_type matcher still guards the lane: a documentation task
    assigned to devops-1 is a misassignment, warned + skipped."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    orch = _orch()
    task = _assigned_devops_task(task_type="documentation")
    with (
        patch.object(orch, "_resolve_agent_slug", return_value="devops-1"),
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_spawn_pending_dev", new=AsyncMock()) as pending,
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dev_dispatch_one(_client(), task)

    spawn.assert_not_awaited()
    pending.assert_not_awaited()


def test_readiness_gate_devops_on_needs_revision_flag_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spawn readiness gate must accept the devops owner on its own
    rework task when armed, and keep today's refusal when not."""
    reason_off = AgentOrchestrator._readiness_check_role_for_status(
        agent_id="devops-1", role="devops", status="needs_revision"
    )
    assert reason_off is not None
    monkeypatch.setattr(settings, "devops_enabled", True)
    assert (
        AgentOrchestrator._readiness_check_role_for_status(
            agent_id="devops-1", role="devops", status="needs_revision"
        )
        is None
    )
    assert (
        AgentOrchestrator._readiness_check_role_for_status(
            agent_id="devops-1", role="devops", status="verifying"
        )
        is None
    )


def test_readiness_gate_devops_widening_additive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The widening is additive: QA stays a misroute even when armed."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    reason = AgentOrchestrator._readiness_check_role_for_status(
        agent_id="be-qa", role="qa", status="needs_revision"
    )
    assert reason is not None

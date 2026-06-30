"""A re-park during probe-success resume must not orphan the agent. The waiting
record must stay until ``spawn_agent`` actually launches a container — deleting
it before the spawn lets a provider re-park (``spawn_agent`` bails OFFLINE on
the parked-provider short-circuit) leave the agent with no record for the
probe-resume loop to revive. #71: a successful launch no longer tears the record
down immediately either — it is kept past the launch and torn down only once
liveness is confirmed, so a container that launches then dies immediately is
re-resumed by the orphan fallback within a tick instead of stranding for the
reaper's TTL.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.models.runtime import AgentInstance, WaitingRecord
from roboco.runtime.orchestrator import (
    AgentOrchestrator,
    AgentReadinessError,
    AgentState,
)


def _make_orchestrator() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._waiting_records = {}
    orch._instances = {}
    orch._bg_tasks = set()
    orch._resume_confirm_delay = 0.0  # deterministic confirmations in tests
    return orch


def _record(agent_id: str = "be-dev-1", provider: str = "anthropic") -> WaitingRecord:
    return WaitingRecord(
        agent_id=agent_id,
        task_id=str(uuid4()),
        waiting_for="rate_limit_lifted",
        waiting_since=datetime.now(UTC),
        context={"provider": provider},
    )


def _instance(state: AgentState) -> AgentInstance:
    cfg = type("C", (), {"provider_type": "anthropic", "model": "opus"})()
    return AgentInstance(agent_id="be-dev-1", state=state, config=cfg)


class TestResolveWaitReparkKeepsRecord:
    async def test_record_kept_when_spawn_bails_offline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A re-park bails spawn with an OFFLINE instance — the record must stay
        so the next probe-success re-attempts the resume (not orphan the agent)."""
        orch = _make_orchestrator()
        orch._waiting_records = {"be-dev-1": _record()}
        offline = _instance(AgentState.OFFLINE)
        monkeypatch.setattr(orch, "spawn_agent", AsyncMock(return_value=offline))
        delete_mock = AsyncMock()
        monkeypatch.setattr(orch, "_delete_waiting_record", delete_mock)

        result = await orch.resolve_wait(
            "be-dev-1", {"reason": "rate_limit_lifted", "provider": "anthropic"}
        )

        assert result is offline
        # Record kept — the probe-resume loop can re-attempt on the next clear.
        assert "be-dev-1" in orch._waiting_records
        delete_mock.assert_not_awaited()

    async def test_record_kept_past_launch_until_liveness_confirmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#71: a successful launch no longer tears the record down synchronously.
        It is kept past the launch and a liveness confirmation is scheduled — so a
        container that launches then dies immediately keeps its record for the
        orphan fallback instead of stranding the task until the reaper's TTL."""
        orch = _make_orchestrator()
        orch._waiting_records = {"be-dev-1": _record()}
        active = _instance(AgentState.ACTIVE)
        monkeypatch.setattr(orch, "spawn_agent", AsyncMock(return_value=active))
        # False at the pre-spawn guard, True once liveness is confirmed.
        calls = {"n": 0}

        def _active(_aid: str) -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        monkeypatch.setattr(orch, "_is_agent_active", _active)
        delete_mock = AsyncMock()
        monkeypatch.setattr(orch, "_delete_waiting_record", delete_mock)

        result = await orch.resolve_wait(
            "be-dev-1", {"reason": "rate_limit_lifted", "provider": "anthropic"}
        )

        assert result is active
        # Record NOT torn down on the bare launch — confirmation owns the delete.
        assert "be-dev-1" in orch._waiting_records
        delete_mock.assert_not_awaited()
        # The scheduled confirmation deletes it once liveness is confirmed.
        await asyncio.gather(*orch._bg_tasks, return_exceptions=True)
        assert "be-dev-1" not in orch._waiting_records
        delete_mock.assert_awaited_once_with("be-dev-1")

    async def test_record_kept_when_respawned_container_dies_immediately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#71: the gap — a container that launches then dies immediately must keep
        its record so the probe-resume orphan fallback re-resumes within a tick,
        not strand the task for the reaper's TTL. Liveness confirm sees the agent
        no longer active and leaves the record in place."""
        orch = _make_orchestrator()
        orch._waiting_records = {"be-dev-1": _record()}
        active = _instance(AgentState.ACTIVE)
        monkeypatch.setattr(orch, "spawn_agent", AsyncMock(return_value=active))
        # At confirmation time the container has died (health loop marked it).
        monkeypatch.setattr(orch, "_is_agent_active", lambda _aid: False)
        delete_mock = AsyncMock()
        monkeypatch.setattr(orch, "_delete_waiting_record", delete_mock)

        result = await orch.resolve_wait(
            "be-dev-1", {"reason": "rate_limit_lifted", "provider": "anthropic"}
        )

        assert result is active
        await asyncio.gather(*orch._bg_tasks, return_exceptions=True)
        # Record survives — the orphan fallback can re-resume on the next tick.
        assert "be-dev-1" in orch._waiting_records
        delete_mock.assert_not_awaited()

    async def test_active_agent_guard_prevents_double_spawn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#71: a lingering record (a prior resume not yet confirmed) must not
        double-spawn an already-active agent. resolve_wait returns None and never
        calls spawn_agent."""
        orch = _make_orchestrator()
        orch._waiting_records = {"be-dev-1": _record()}
        monkeypatch.setattr(orch, "_is_agent_active", lambda _aid: True)
        spawn = AsyncMock()
        monkeypatch.setattr(orch, "spawn_agent", spawn)

        result = await orch.resolve_wait(
            "be-dev-1", {"reason": "rate_limit_lifted", "provider": "anthropic"}
        )

        assert result is None
        spawn.assert_not_awaited()

    async def test_record_deleted_when_spawn_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A spawn failure (e.g. readiness refused → task auto-blocked) tears
        down the record so the probe loop doesn't keep re-resuming a task that
        moved to a different state. Matches the pre-fix behavior (the record
        was deleted before the spawn attempt)."""
        orch = _make_orchestrator()
        orch._waiting_records = {"be-dev-1": _record()}

        async def _boom(*_a: Any, **_k: Any) -> AgentInstance:
            raise AgentReadinessError("not ready")

        monkeypatch.setattr(orch, "spawn_agent", _boom)
        delete_mock = AsyncMock()
        monkeypatch.setattr(orch, "_delete_waiting_record", delete_mock)

        with pytest.raises(AgentReadinessError):
            await orch.resolve_wait(
                "be-dev-1", {"reason": "rate_limit_lifted", "provider": "anthropic"}
            )

        assert "be-dev-1" not in orch._waiting_records
        delete_mock.assert_awaited_once_with("be-dev-1")

    async def test_record_kept_across_repeated_offline_bails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two probe-success resumes that both bail (provider flaps) must both
        find the record still present — the agent is never orphaned across the
        flap, and the durable delete is never called."""
        orch = _make_orchestrator()
        orch._waiting_records = {"be-dev-1": _record()}
        offline = _instance(AgentState.OFFLINE)
        monkeypatch.setattr(orch, "spawn_agent", AsyncMock(return_value=offline))
        delete_mock = AsyncMock()
        monkeypatch.setattr(orch, "_delete_waiting_record", delete_mock)

        for _ in range(3):
            await orch.resolve_wait(
                "be-dev-1", {"reason": "rate_limit_lifted", "provider": "anthropic"}
            )

        assert "be-dev-1" in orch._waiting_records
        delete_mock.assert_not_awaited()

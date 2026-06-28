"""F098: a re-park during probe-success resume must not orphan the agent.

``resolve_wait`` deletes the waiting record (in-memory + durable) and then calls
``spawn_agent`` to respawn the parked agent. If the provider re-parks in the
window between the probe-success clear and the spawn (the rate limit lifts then
immediately re-limits, or a second provider limit lands), ``spawn_agent`` bails
with an OFFLINE instance — the F095 parked-provider short-circuit. The old order
deleted the record BEFORE the spawn, so a bail orphaned the agent: no record
means the probe-resume loop can never revive it and the spawn gate bails every
tick. The record must stay until a container actually launches.
"""

from __future__ import annotations

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

    async def test_record_deleted_when_spawn_launches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A successful launch (ACTIVE) tears down the record as before."""
        orch = _make_orchestrator()
        orch._waiting_records = {"be-dev-1": _record()}
        active = _instance(AgentState.ACTIVE)
        monkeypatch.setattr(orch, "spawn_agent", AsyncMock(return_value=active))
        delete_mock = AsyncMock()
        monkeypatch.setattr(orch, "_delete_waiting_record", delete_mock)

        result = await orch.resolve_wait(
            "be-dev-1", {"reason": "rate_limit_lifted", "provider": "anthropic"}
        )

        assert result is active
        assert "be-dev-1" not in orch._waiting_records
        delete_mock.assert_awaited_once_with("be-dev-1")

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

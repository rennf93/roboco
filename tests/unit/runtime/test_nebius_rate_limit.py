"""NEBIUS 429/auth parking: same exit-code convention as kimi/codex/grok,
scoped to ModelProvider.NEBIUS so a numeric-code collision with another
provider's crash can never mis-park (see ``_NEBIUS_RATE_LIMIT_EXIT_CODE`` /
``_NEBIUS_AUTH_EXIT_CODE`` in ``roboco.runtime.orchestrator``). The park
metadata must surface the provider-specific reason (provider "nebius" +
kind "rate_limited"/"auth_missing").
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import (
    _NEBIUS_AUTH_EXIT_CODE,
    _NEBIUS_RATE_LIMIT_EXIT_CODE,
    AgentOrchestrator,
    AgentState,
)


def _nebius_instance(provider_type: str = "nebius") -> AgentInstance:
    cfg = type(
        "C",
        (),
        {
            "provider_type": provider_type,
            "model": "nvidia/nemotron-3-super-120b-a12b",
        },
    )()
    inst = AgentInstance(agent_id="be-dev-1", state=AgentState.ACTIVE, config=cfg)
    inst.current_task_id = "task-1"
    inst.container_id = "cid"
    return inst


class _FakeTracker:
    def __init__(self) -> None:
        self.activated_with: dict[str, object] | None = None

    async def activate(
        self,
        *,
        retry_after: float,
        affected_agents: list[str],
        kind: str = "rate_limited",
    ) -> None:
        self.activated_with = {
            "retry_after": retry_after,
            "affected_agents": affected_agents,
            "kind": kind,
        }


def test_is_nebius_rate_limit_exit() -> None:
    inst = _nebius_instance()
    assert AgentOrchestrator._is_nebius_rate_limit_exit(
        inst, _NEBIUS_RATE_LIMIT_EXIT_CODE
    )
    assert not AgentOrchestrator._is_nebius_rate_limit_exit(inst, 0)
    assert not AgentOrchestrator._is_nebius_rate_limit_exit(inst, 1)
    # A kimi exit at the SAME numeric code must NOT be classified as nebius.
    assert not AgentOrchestrator._is_nebius_rate_limit_exit(
        _nebius_instance(provider_type="kimi"), _NEBIUS_RATE_LIMIT_EXIT_CODE
    )
    assert not AgentOrchestrator._is_nebius_rate_limit_exit(
        _nebius_instance(provider_type="anthropic"),
        _NEBIUS_RATE_LIMIT_EXIT_CODE,
    )


def test_is_nebius_auth_exit() -> None:
    inst = _nebius_instance()
    assert AgentOrchestrator._is_nebius_auth_exit(inst, _NEBIUS_AUTH_EXIT_CODE)
    assert not AgentOrchestrator._is_nebius_auth_exit(inst, 0)
    assert not AgentOrchestrator._is_nebius_auth_exit(inst, 1)
    assert not AgentOrchestrator._is_nebius_auth_exit(
        _nebius_instance(provider_type="kimi"), _NEBIUS_AUTH_EXIT_CODE
    )


@pytest.mark.asyncio
async def test_park_nebius_rate_limited_activates_and_offlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._waiting_records = {}
    inst = _nebius_instance()
    inst.error_count = 2  # pretend prior crashes - parking must NOT count one
    tracker = _FakeTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())

    await orch._park_nebius_rate_limited("be-dev-1", inst)

    finalize.assert_awaited_once()
    assert inst.state == AgentState.OFFLINE
    assert inst.container_id is None
    assert inst.error_count == 0  # a 429 is not a crash
    assert tracker.activated_with == {
        "retry_after": pytest.approx(60.0),
        "affected_agents": ["be-dev-1"],
        "kind": "rate_limited",
    }
    # Park metadata surfaces the provider-specific reason.
    record = orch._waiting_records["be-dev-1"]
    assert record.context == {"provider": "nebius", "kind": "rate_limited"}
    assert record.task_id == "task-1"


@pytest.mark.asyncio
async def test_park_nebius_auth_unavailable_activates_with_auth_missing_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._waiting_records = {}
    inst = _nebius_instance()
    inst.error_count = 2
    tracker = _FakeTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())

    await orch._park_nebius_auth_unavailable("be-dev-1", inst)

    assert inst.state == AgentState.OFFLINE
    assert inst.container_id is None
    assert inst.error_count == 0
    assert tracker.activated_with == {
        "retry_after": pytest.approx(60.0),
        "affected_agents": ["be-dev-1"],
        "kind": "auth_missing",
    }
    # Park metadata surfaces the provider-specific reason.
    record = orch._waiting_records["be-dev-1"]
    assert record.context == {"provider": "nebius", "kind": "auth_missing"}


@pytest.mark.asyncio
async def test_handle_stopped_container_parks_on_nebius_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    inst = _nebius_instance()
    park = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_park_nebius_rate_limited", park)
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)

    await orch._handle_stopped_container("be-dev-1", inst, _NEBIUS_RATE_LIMIT_EXIT_CODE)

    park.assert_awaited_once_with("be-dev-1", inst)
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_stopped_container_parks_on_nebius_auth_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    inst = _nebius_instance()
    park = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_park_nebius_auth_unavailable", park)
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)

    await orch._handle_stopped_container("be-dev-1", inst, _NEBIUS_AUTH_EXIT_CODE)

    park.assert_awaited_once_with("be-dev-1", inst)
    finalize.assert_not_awaited()

"""CODEX 429/auth parking: same exit-code convention as grok, scoped to
ModelProvider.OPENAI so a numeric-code collision with another provider's crash
can never mis-park (see ``_CODEX_RATE_LIMIT_EXIT_CODE`` / ``_CODEX_AUTH_EXIT_CODE``
in ``roboco.runtime.orchestrator``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import (
    _CODEX_AUTH_EXIT_CODE,
    _CODEX_RATE_LIMIT_EXIT_CODE,
    AgentOrchestrator,
    AgentState,
)


def _codex_instance(provider_type: str = "openai") -> AgentInstance:
    cfg = type("C", (), {"provider_type": provider_type, "model": "gpt-5.3-codex"})()
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


def test_is_codex_rate_limit_exit() -> None:
    inst = _codex_instance()
    assert AgentOrchestrator._is_codex_rate_limit_exit(
        inst, _CODEX_RATE_LIMIT_EXIT_CODE
    )
    assert not AgentOrchestrator._is_codex_rate_limit_exit(inst, 0)
    assert not AgentOrchestrator._is_codex_rate_limit_exit(inst, 1)
    # A grok exit at the SAME numeric code must NOT be classified as codex.
    assert not AgentOrchestrator._is_codex_rate_limit_exit(
        _codex_instance(provider_type="grok"), _CODEX_RATE_LIMIT_EXIT_CODE
    )
    assert not AgentOrchestrator._is_codex_rate_limit_exit(
        _codex_instance(provider_type="anthropic"), _CODEX_RATE_LIMIT_EXIT_CODE
    )


def test_is_codex_auth_exit() -> None:
    inst = _codex_instance()
    assert AgentOrchestrator._is_codex_auth_exit(inst, _CODEX_AUTH_EXIT_CODE)
    assert not AgentOrchestrator._is_codex_auth_exit(inst, 0)
    assert not AgentOrchestrator._is_codex_auth_exit(inst, 1)
    assert not AgentOrchestrator._is_codex_auth_exit(
        _codex_instance(provider_type="grok"), _CODEX_AUTH_EXIT_CODE
    )


@pytest.mark.asyncio
async def test_park_codex_rate_limited_activates_and_offlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._waiting_records = {}
    orch._rate_limit_ceo_notified = set()
    inst = _codex_instance()
    inst.error_count = 2  # pretend prior crashes — parking must NOT count one
    tracker = _FakeTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())

    await orch._park_codex_rate_limited("be-dev-1", inst)

    finalize.assert_awaited_once()
    assert inst.state == AgentState.OFFLINE
    assert inst.container_id is None
    assert inst.error_count == 0  # a 429 is not a crash
    assert tracker.activated_with == {
        "retry_after": pytest.approx(60.0),
        "affected_agents": ["be-dev-1"],
        "kind": "rate_limited",
    }


@pytest.mark.asyncio
async def test_park_codex_auth_unavailable_activates_with_auth_missing_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._waiting_records = {}
    orch._rate_limit_ceo_notified = set()
    inst = _codex_instance()
    inst.error_count = 2
    tracker = _FakeTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())

    await orch._park_codex_auth_unavailable("be-dev-1", inst)

    assert inst.state == AgentState.OFFLINE
    assert inst.container_id is None
    assert inst.error_count == 0
    assert tracker.activated_with == {
        "retry_after": pytest.approx(60.0),
        "affected_agents": ["be-dev-1"],
        "kind": "auth_missing",
    }


@pytest.mark.asyncio
async def test_handle_stopped_container_parks_on_codex_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    inst = _codex_instance()
    park = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_park_codex_rate_limited", park)
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)

    await orch._handle_stopped_container("be-dev-1", inst, _CODEX_RATE_LIMIT_EXIT_CODE)

    park.assert_awaited_once_with("be-dev-1", inst)
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_stopped_container_parks_on_codex_auth_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    inst = _codex_instance()
    park = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_park_codex_auth_unavailable", park)
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)

    await orch._handle_stopped_container("be-dev-1", inst, _CODEX_AUTH_EXIT_CODE)

    park.assert_awaited_once_with("be-dev-1", inst)
    finalize.assert_not_awaited()

"""Reaper releases tasks whose last_heartbeat_at exceeds the TTL.

The orchestrator dispatcher periodically calls `_reap_stale_claims` to
release tasks whose holder has gone silent past the heartbeat TTL. The
schema-level column `last_heartbeat_at` (DateTime(timezone=True)) has
existed since migration 006; this test covers the runtime decision that
turns a stale heartbeat into a freed claim.

Datetimes used here are timezone-aware UTC because the underlying column
is tz-aware — comparing naive vs aware would raise TypeError in production
even though it would silently work against an in-memory mock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.models.runtime import AgentInstance, WaitingRecord
from roboco.runtime.orchestrator import AgentOrchestrator, AgentState
from roboco.seeds.initial_data import AGENT_UUIDS


@pytest.mark.asyncio
async def test_reap_stale_claims_releases_dead_holders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task past TTL is unclaimed; a fresh one is left alone."""
    stale_id = uuid4()
    fresh_id = uuid4()
    now = datetime.now(UTC)
    stale_task = type(
        "T",
        (),
        {"id": stale_id, "last_heartbeat_at": now - timedelta(seconds=600)},
    )()
    fresh_task = type(
        "T",
        (),
        {"id": fresh_id, "last_heartbeat_at": now - timedelta(seconds=10)},
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)  # bypass __init__
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [stale_task, fresh_task]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    svc.unclaim_for_reaper.assert_awaited_once_with(stale_id)


@pytest.mark.asyncio
async def test_reap_stale_claims_releases_holders_with_null_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claimed task that never heartbeated (NULL column) is treated as stale."""
    null_id = uuid4()
    null_task = type("T", (), {"id": null_id, "last_heartbeat_at": None})()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [null_task]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    svc.unclaim_for_reaper.assert_awaited_once_with(null_id)


@pytest.mark.asyncio
async def test_reap_stale_claims_swallows_unclaim_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unclaim_for_reaper failure must not abort the reap loop."""
    stale_a = uuid4()
    stale_b = uuid4()
    now = datetime.now(UTC)
    task_a = type(
        "T", (), {"id": stale_a, "last_heartbeat_at": now - timedelta(seconds=600)}
    )()
    task_b = type(
        "T", (), {"id": stale_b, "last_heartbeat_at": now - timedelta(seconds=900)}
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [task_a, task_b]
    svc.unclaim_for_reaper = AsyncMock(side_effect=[RuntimeError("transient"), None])

    await orch._reap_with_service(svc)

    # Both stale tasks attempted; second succeeded despite first raising.
    expected_attempts = 2
    assert svc.unclaim_for_reaper.await_count == expected_attempts


@pytest.mark.asyncio
async def test_reap_spares_claims_whose_assignee_container_is_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale-heartbeat task is NOT reaped while its assignee container lives.

    A developer deep in a long edit/test cycle outruns the heartbeat TTL; the
    running container is the ground truth, so the claim survives rather than
    being churned out from under live work. A peer task whose assignee has no
    live instance is still reaped.
    """
    now = datetime.now(UTC)
    live_id = uuid4()
    dead_id = uuid4()
    live_task = type(
        "T",
        (),
        {
            "id": live_id,
            "last_heartbeat_at": now - timedelta(seconds=600),
            "assigned_to": AGENT_UUIDS["be-dev-1"],
            "claimed_by": None,
        },
    )()
    dead_task = type(
        "T",
        (),
        {
            "id": dead_id,
            "last_heartbeat_at": now - timedelta(seconds=600),
            "assigned_to": AGENT_UUIDS["be-dev-2"],
            "claimed_by": None,
        },
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    orch._instances = {
        "be-dev-1": AgentInstance(agent_id="be-dev-1", state=AgentState.ACTIVE)
    }
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [live_task, dead_task]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    # The live-assignee task is spared; only the dead one is reaped.
    svc.unclaim_for_reaper.assert_awaited_once_with(dead_id)


def _grok_instance() -> AgentInstance:
    cfg = type("C", (), {"provider_type": "grok"})()
    return AgentInstance(agent_id="be-dev-1", state=AgentState.ACTIVE, config=cfg)


@pytest.mark.asyncio
async def test_reaper_kills_and_releases_wedged_grok_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GROK container idle past the kill TTL is killed, evicted, and released.

    Unlike a Claude agent, a wedged grok container is ACTIVE yet fires no
    verb, so the live-instance skip would shield it forever. Past the longer
    grok-idle TTL the watchdog removes the container and drops it from
    `_instances`, so the same reap pass then unclaims the task.
    """
    now = datetime.now(UTC)
    task_id = uuid4()
    wedged = type(
        "T",
        (),
        {
            "id": task_id,
            "last_heartbeat_at": now - timedelta(seconds=1200),
            "assigned_to": AGENT_UUIDS["be-dev-1"],
            "claimed_by": None,
        },
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    orch._grok_idle_kill_ttl = 900
    orch._instances = {"be-dev-1": _grok_instance()}
    remove_mock = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove_mock)
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [wedged]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    remove_mock.assert_awaited_once_with("roboco-agent-be-dev-1")
    assert "be-dev-1" not in orch._instances  # evicted
    svc.unclaim_for_reaper.assert_awaited_once_with(task_id)  # released


@pytest.mark.asyncio
async def test_reaper_spares_grok_container_within_kill_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GROK container stale past the claim TTL but within the kill TTL lives.

    Only a truly-dead run (idle past the longer grok-idle TTL) is killed — a
    slow-but-working agent is left alone.
    """
    now = datetime.now(UTC)
    recent = type(
        "T",
        (),
        {
            "id": uuid4(),
            "last_heartbeat_at": now - timedelta(seconds=600),
            "assigned_to": AGENT_UUIDS["be-dev-1"],
            "claimed_by": None,
        },
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    orch._grok_idle_kill_ttl = 900
    orch._instances = {"be-dev-1": _grok_instance()}
    remove_mock = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove_mock)
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [recent]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    remove_mock.assert_not_awaited()
    assert "be-dev-1" in orch._instances
    svc.unclaim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaper_never_kills_non_grok_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-GROK (Claude) container idle past the kill TTL is still spared.

    The watchdog only kills GROK runtimes; a quiet Claude agent keeps the
    heartbeat-skip protection regardless of how long it has been silent.
    """
    now = datetime.now(UTC)
    claude_task = type(
        "T",
        (),
        {
            "id": uuid4(),
            "last_heartbeat_at": now - timedelta(seconds=1200),
            "assigned_to": AGENT_UUIDS["be-dev-1"],
            "claimed_by": None,
        },
    )()
    claude_cfg = type("C", (), {"provider_type": "anthropic"})()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    orch._grok_idle_kill_ttl = 900
    orch._instances = {
        "be-dev-1": AgentInstance(
            agent_id="be-dev-1", state=AgentState.ACTIVE, config=claude_cfg
        )
    }
    remove_mock = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove_mock)
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [claude_task]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    remove_mock.assert_not_awaited()
    assert "be-dev-1" in orch._instances
    svc.unclaim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_reap_spares_live_container_on_registry_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry amnesia: the orchestrator forgot the instance (empty `_instances`,
    e.g. after a restart) but the container is still running per Docker — the
    task must NOT be reaped. This closes the be-dev-1 over-reap: a live agent the
    orchestrator merely lost track of is no longer churned out from under work.
    """
    now = datetime.now(UTC)
    task_id = uuid4()
    task = type(
        "T",
        (),
        {
            "id": task_id,
            "last_heartbeat_at": now - timedelta(seconds=600),
            "assigned_to": AGENT_UUIDS["be-dev-1"],
            "claimed_by": None,
        },
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    orch._grok_idle_kill_ttl = 900
    orch._instances = {}  # registry lost; container still up
    monkeypatch.setattr(
        orch, "_inspect_container_state", AsyncMock(return_value=(True, None))
    )
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [task]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    svc.unclaim_for_reaper.assert_not_awaited()  # spared — Docker says it's alive


@pytest.mark.asyncio
async def test_reap_releases_on_registry_miss_when_container_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry miss AND Docker says the container is gone → genuinely dead → reaped."""
    now = datetime.now(UTC)
    task_id = uuid4()
    task = type(
        "T",
        (),
        {
            "id": task_id,
            "last_heartbeat_at": now - timedelta(seconds=600),
            "assigned_to": AGENT_UUIDS["be-dev-1"],
            "claimed_by": None,
        },
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    orch._grok_idle_kill_ttl = 900
    orch._instances = {}
    monkeypatch.setattr(
        orch, "_inspect_container_state", AsyncMock(return_value=(False, 0))
    )
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [task]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    svc.unclaim_for_reaper.assert_awaited_once_with(task_id)


@pytest.mark.asyncio
async def test_reap_spares_provider_parked_agent_for_probe_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F035: a provider-parked agent (dead container, OFFLINE, with a
    ``rate_limit_lifted`` WaitingRecord) must NOT be reaped by the stale-claim
    reaper. The probe-resume loop owns its recovery and respawns it when the
    provider recovers; reaping would release the claim to pending, and then
    probe-success would respawn the agent on a task it no longer owns.
    """
    now = datetime.now(UTC)
    task_id = uuid4()
    task = type(
        "T",
        (),
        {
            "id": task_id,
            "last_heartbeat_at": now - timedelta(seconds=1200),
            "assigned_to": AGENT_UUIDS["be-dev-1"],
            "claimed_by": None,
        },
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    orch._grok_idle_kill_ttl = 900
    orch._instances = {}  # parked agent is OFFLINE / not in the registry
    orch._waiting_records = {
        "be-dev-1": WaitingRecord(
            agent_id="be-dev-1",
            task_id=str(task_id),
            waiting_for="rate_limit_lifted",
            waiting_since=now,
            context={"provider": "anthropic"},
        )
    }
    monkeypatch.setattr(
        orch, "_inspect_container_state", AsyncMock(return_value=(False, 0))
    )
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [task]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    svc.unclaim_for_reaper.assert_not_awaited()  # spared for the probe loop


@pytest.mark.asyncio
async def test_registry_uninitialised_skips_docker_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With `_instances` never initialised (None — the __new__ unit harness), the
    Docker fallback is skipped and the stale task reaps as before; no accidental
    Docker probing where there's no registry to be amnesiac about.
    """
    now = datetime.now(UTC)
    task_id = uuid4()
    task = type(
        "T",
        (),
        {
            "id": task_id,
            "last_heartbeat_at": now - timedelta(seconds=600),
            "assigned_to": AGENT_UUIDS["be-dev-1"],
            "claimed_by": None,
        },
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    # _instances intentionally NOT set -> getattr yields None -> no fallback.
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [task]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    svc.unclaim_for_reaper.assert_awaited_once_with(task_id)

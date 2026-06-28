"""Human-only roles (CEO / prompter / secretary) are NEVER spawned.

The CEO is the human operator, not a container; intake (prompter) and
secretary are human-driven interactive chats launched through their own
guarded paths (_spawn_intake_container / _spawn_secretary_container). A
live 2026-06-27 incident saw a CEO container spawned by _dispatch_a2a_work:
that dispatcher iterates every A2A/notification target and spawns it, and
`_is_agent_active("ceo")` is *always* false (the CEO is never a container),
so the "skip if active" check could never protect the CEO — any
CEO-addressed notification launched a CEO container (the system acting as
the human CEO: a trust violation).

The fix is a single chokepoint guard at the top of `spawn_agent` that
refuses Role.CEO / PROMPTER / SECRETARY, plus a defense-in-depth skip in
`_dispatch_a2a_work` so the dispatcher never even calls in for a human
target (avoids error-log spam — the notification stays for the human to
read in the panel). The chokepoint is structural: every dispatcher present
or future is covered, because they all go through `spawn_agent`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.foundation.identity import Role, role_for_slug
from roboco.runtime.orchestrator import AgentOrchestrator, AgentReadinessError
from roboco.seeds.initial_data import AGENT_UUIDS


def _orch() -> AgentOrchestrator:
    # The human-role guard is the first statement in spawn_agent and only
    # consults the pure `role_for_slug` + the module logger — no self state
    # — so a bare (un-initialized) orchestrator is sufficient to exercise it.
    orch = object.__new__(AgentOrchestrator)
    return orch


# ---------------------------------------------------------------------------
# spawn_agent chokepoint — refuses human-only roles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "human_slug",
    ["ceo", "intake-1", "secretary-1"],
)
@pytest.mark.asyncio
async def test_spawn_agent_refuses_human_only_roles(human_slug: str) -> None:
    """spawn_agent must refuse the CEO / prompter / secretary — a dispatcher
    must never launch a container for a human-only role."""
    orch = _orch()

    with pytest.raises(AgentReadinessError, match="human-only role"):
        await orch.spawn_agent(human_slug)

    # Sanity: the slug really is a human-only role (guards against the test
    # silently passing because role_for_slug returned something unexpected).
    assert role_for_slug(human_slug) in (Role.CEO, Role.PROMPTER, Role.SECRETARY)


@pytest.mark.asyncio
async def test_spawn_agent_does_not_refuse_real_agent() -> None:
    """A real (container-eligible) agent must NOT trip the human-role guard.

    It may still be refused by the downstream readiness gate, but the
    refusal must NOT carry the human-only-role message. This proves the
    guard is scoped to human roles only and doesn't over-block the fleet.
    """
    orch = _orch()

    # Stub the readiness gate so we isolate the human-role guard: a real
    # agent passes the guard and reaches (and is stopped by) readiness,
    # which is a *different* refusal than the human-role one.
    async def _ready(_aid: str, _tid: str | None) -> str | None:
        return "stubbed-not-ready"

    orch._readiness_gate = _ready  # type: ignore[assignment]

    with pytest.raises(AgentReadinessError) as exc_info:
        await orch.spawn_agent("be-dev-1", task_id="t-1")

    assert "human-only role" not in str(exc_info.value)
    assert role_for_slug("be-dev-1") not in (Role.CEO, Role.PROMPTER, Role.SECRETARY)


# ---------------------------------------------------------------------------
# _dispatch_a2a_work — skips human-only targets (defense-in-depth)
# ---------------------------------------------------------------------------


def _a2a_orch(ceo_uuid: str) -> AgentOrchestrator:
    """A bare orchestrator with the a2a-dispatch collaborators stubbed."""
    orch = object.__new__(AgentOrchestrator)
    # _dispatch_a2a_work consults: _fetch_notifications, _resolve_agent_slug,
    # _is_agent_active, spawn_agent. _resolve_agent_slug is pure (module
    # UUID_TO_SLUG) so it works unstubbed; stub the rest.
    orch.spawn_agent = AsyncMock()  # type: ignore[method-assign]
    orch._is_agent_active = MagicMock(return_value=False)  # type: ignore[method-assign]
    orch._fetch_notifications = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {"id": "n1", "to_agents": [ceo_uuid], "body": "board handoff"},
        ]
    )
    return orch


@pytest.mark.asyncio
async def test_dispatch_a2a_skips_ceo_target() -> None:
    """A CEO-addressed A2A notification must NOT spawn a CEO container.

    The CEO being a notification target (board-review handoff, escalation)
    is expected; it is NOT a spawn signal. The notification stays for the
    human to read in the panel. This is the live 2026-06-27 regression.
    """
    ceo_uuid = AGENT_UUIDS["ceo"]
    orch = _a2a_orch(ceo_uuid)
    client = MagicMock()

    await orch._dispatch_a2a_work(client)

    orch.spawn_agent.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dispatch_a2a_skips_intake_and_secretary_targets() -> None:
    """Intake (prompter) and secretary are human-driven chats — never spawned."""
    for slug in ("intake-1", "secretary-1"):
        orch = _a2a_orch(AGENT_UUIDS[slug])
        await orch._dispatch_a2a_work(MagicMock())
        orch.spawn_agent.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dispatch_a2a_still_spawns_real_agent_target() -> None:
    """A real (container-eligible) A2A target is still dispatched — the
    skip is scoped to human roles only and does not suppress real A2A."""
    be_uuid = AGENT_UUIDS["be-dev-1"]
    orch = _a2a_orch(be_uuid)
    client = MagicMock()

    await orch._dispatch_a2a_work(client)

    orch.spawn_agent.assert_awaited_once()  # type: ignore[attr-defined]
    _args, kwargs = orch.spawn_agent.call_args  # type: ignore[attr-defined]
    assert kwargs.get("agent_id") == "be-dev-1"


@pytest.mark.asyncio
async def test_dispatch_a2a_mixed_targets_skips_only_human() -> None:
    """A notification addressed to both the CEO and a real agent spawns the
    real agent once and never the CEO."""
    ceo_uuid = AGENT_UUIDS["ceo"]
    be_uuid = AGENT_UUIDS["be-dev-1"]
    orch = object.__new__(AgentOrchestrator)
    orch.spawn_agent = AsyncMock()  # type: ignore[method-assign]
    orch._is_agent_active = MagicMock(return_value=False)  # type: ignore[method-assign]
    orch._fetch_notifications = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"id": "n1", "to_agents": [ceo_uuid, be_uuid]}]
    )
    client = MagicMock()

    await orch._dispatch_a2a_work(client)

    spawned = [c.kwargs.get("agent_id") for c in orch.spawn_agent.call_args_list]
    assert "ceo" not in spawned
    assert spawned == ["be-dev-1"]

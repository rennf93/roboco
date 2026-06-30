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

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import roboco.runtime.orchestrator as orch_mod
from roboco.foundation.identity import Role, role_for_slug
from roboco.runtime.orchestrator import AgentOrchestrator, AgentReadinessError
from roboco.seeds.initial_data import AGENT_UUIDS


def _orch() -> Any:
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

    orch._readiness_gate = _ready

    with pytest.raises(AgentReadinessError) as exc_info:
        await orch.spawn_agent("be-dev-1", task_id="t-1")

    assert "human-only role" not in str(exc_info.value)
    assert role_for_slug("be-dev-1") not in (Role.CEO, Role.PROMPTER, Role.SECRETARY)


# ---------------------------------------------------------------------------
# _dispatch_a2a_work — skips human-only targets (defense-in-depth)
# ---------------------------------------------------------------------------


def _a2a_orch(ceo_uuid: str) -> Any:
    """A bare orchestrator with the a2a-dispatch collaborators stubbed."""
    orch: Any = object.__new__(AgentOrchestrator)
    # _dispatch_a2a_work consults: _fetch_notifications, _resolve_agent_slug,
    # _is_agent_active, spawn_agent. _resolve_agent_slug is pure (module
    # UUID_TO_SLUG) so it works unstubbed; stub the rest.
    orch.spawn_agent = AsyncMock()
    orch._is_agent_active = MagicMock(return_value=False)
    orch._fetch_notifications = AsyncMock(
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

    orch.spawn_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_a2a_skips_intake_and_secretary_targets() -> None:
    """Intake (prompter) and secretary are human-driven chats — never spawned."""
    for slug in ("intake-1", "secretary-1"):
        orch = _a2a_orch(AGENT_UUIDS[slug])
        await orch._dispatch_a2a_work(MagicMock())
        orch.spawn_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_a2a_surfaces_human_only_target_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#75: an a2a targeting a human-only role is skipped (not spawned) BUT
    surfaced in the dispatch log — not silently dropped — so an a2a expecting a
    human-side action (a CEO sign-off relay) is visible to the operator. We
    record on the module logger directly (structlog output stream is set up
    globally and not reliably capsys-captured when the full suite reconfigures
    it)."""
    info_calls: list[str] = []

    class _Logger:
        def info(self, msg: str, *_args: object, **_kw: object) -> None:
            info_calls.append(msg)

        # structlog bound loggers expose the full level surface; stub the rest
        # no-op so any incidental log call inside the dispatch path is safe.
        def __getattr__(self, _name: str) -> object:
            return lambda *_a, **_k: None

    monkeypatch.setattr(orch_mod, "logger", _Logger())
    orch = _a2a_orch(AGENT_UUIDS["ceo"])
    await orch._dispatch_a2a_work(MagicMock())
    orch.spawn_agent.assert_not_awaited()
    assert any("human-only role" in m for m in info_calls), (
        "a2a human-only-target skip must be surfaced, not silent"
    )


@pytest.mark.asyncio
async def test_dispatch_a2a_still_spawns_real_agent_target() -> None:
    """A real (container-eligible) A2A target is still dispatched — the
    skip is scoped to human roles only and does not suppress real A2A."""
    be_uuid = AGENT_UUIDS["be-dev-1"]
    orch = _a2a_orch(be_uuid)
    client = MagicMock()

    await orch._dispatch_a2a_work(client)

    orch.spawn_agent.assert_awaited_once()
    _args, kwargs = orch.spawn_agent.call_args
    assert kwargs.get("agent_id") == "be-dev-1"


@pytest.mark.asyncio
async def test_dispatch_a2a_mixed_targets_skips_only_human() -> None:
    """A notification addressed to both the CEO and a real agent spawns the
    real agent once and never the CEO."""
    ceo_uuid = AGENT_UUIDS["ceo"]
    be_uuid = AGENT_UUIDS["be-dev-1"]
    orch: Any = object.__new__(AgentOrchestrator)
    orch.spawn_agent = AsyncMock()
    orch._is_agent_active = MagicMock(return_value=False)
    orch._fetch_notifications = AsyncMock(
        return_value=[{"id": "n1", "to_agents": [ceo_uuid, be_uuid]}]
    )
    client = MagicMock()

    await orch._dispatch_a2a_work(client)

    spawned = [c.kwargs.get("agent_id") for c in orch.spawn_agent.call_args_list]
    assert "ceo" not in spawned
    assert spawned == ["be-dev-1"]


# ---------------------------------------------------------------------------
# _dispatch_pm_review_work — skips a human-only assignee (defense-in-depth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_pm_review_skips_ceo_assignee() -> None:
    """An awaiting_pm_review task assigned to the CEO must NOT respawn a CEO
    container, and must NOT abort the dispatcher's tick (which would stall
    other PM-review respawns behind it). The skip leaves it for the human."""
    orch: Any = object.__new__(AgentOrchestrator)
    orch.spawn_agent = AsyncMock()
    orch._is_agent_active = MagicMock(return_value=False)
    orch._pm_respawn_should_gate = AsyncMock(return_value=False)
    orch._fetch_tasks = AsyncMock(
        return_value=[
            {
                "id": "t1",
                "status": "awaiting_pm_review",
                "team": "backend",
                "assigned_to": AGENT_UUIDS["ceo"],
            }
        ]
    )

    await orch._dispatch_pm_review_work(MagicMock())

    orch.spawn_agent.assert_not_awaited()


# ---------------------------------------------------------------------------
# #49: a stale/ex-human slug must not slip past the layered skip guard
# ---------------------------------------------------------------------------


def _stale_a2a_orch(stale_uuid: str) -> Any:
    """A bare orchestrator whose A2A target resolves to a stale slug.

    ``_resolve_agent_slug`` is pure (module UUID_TO_SLUG); a UUID not in that
    map falls through to ``str(uuid)`` — a slug no longer in AGENTS, i.e. the
    #49 stale-slug case. We pass an arbitrary UUID so the resolved slug is
    not a known agent.
    """
    orch: Any = object.__new__(AgentOrchestrator)
    orch.spawn_agent = AsyncMock()
    orch._is_agent_active = MagicMock(return_value=False)
    orch._fetch_notifications = AsyncMock(
        return_value=[{"id": "n1", "to_agents": [stale_uuid]}]
    )
    return orch


@pytest.mark.asyncio
async def test_dispatch_a2a_skips_stale_ex_human_slug() -> None:
    """#49: a target UUID that resolves to a stale (no longer seeded) slug
    must NOT be spawned. A bare ``role in (CEO, ...)`` test misses this (None
    is not in the tuple), so the dispatcher used to proceed to a doomed spawn
    of a renamed/ex-human slug. is_spawnable_agent_slug closes the hole."""
    # A UUID absent from UUID_TO_SLUG resolves to its own string — not a known
    # agent slug — exercising the stale-slug path.
    orch = _stale_a2a_orch("12345678-1234-1234-1234-123456789abc")

    await orch._dispatch_a2a_work(MagicMock())

    orch.spawn_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pm_review_skips_stale_assignee() -> None:
    """#49: an awaiting_pm_review task whose assignee resolves to a stale
    slug must skip the respawn (not spawn a doomed/ex-human container) and
    must not abort the tick."""
    orch: Any = object.__new__(AgentOrchestrator)
    orch.spawn_agent = AsyncMock()
    orch._is_agent_active = MagicMock(return_value=False)
    orch._pm_respawn_should_gate = AsyncMock(return_value=False)
    orch._fetch_tasks = AsyncMock(
        return_value=[
            {
                "id": "t1",
                "status": "awaiting_pm_review",
                "team": "backend",
                "assigned_to": "12345678-1234-1234-1234-123456789abc",
            }
        ]
    )

    await orch._dispatch_pm_review_work(MagicMock())

    orch.spawn_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_claimed_task_needs_agent_releases_stale_slug_claim() -> None:
    """#49: the reaper path is the *opposite* of the spawn path — a stale-slug
    claim SHOULD be released to pending so a real agent can reclaim it. The
    is_human_only_role check (None -> False) keeps this recovery behaviour."""
    orch: Any = object.__new__(AgentOrchestrator)
    # No instance for the stale slug, and aged past the grace window.
    orch._instances = {}
    orch._is_hitl_blocked = MagicMock(return_value=False)
    orch._time_in_state = MagicMock(return_value=timedelta(seconds=9999))

    needs = orch._claimed_task_needs_agent(
        {
            "assigned_to": "12345678-1234-1234-1234-123456789abc",
            "status": "claimed",
        }
    )
    # The stale slug is returned so the reaper can release the claim — it is
    # NOT suppressed by the human-only skip (which only catches live humans).
    assert needs is not None

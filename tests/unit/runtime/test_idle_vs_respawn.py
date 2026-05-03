"""Closure dispatcher must skip respawn for a parent task that just paused.

Race scenario (audit C12)
-------------------------
``i_am_idle`` runs ``auto_pause_paused_tasks`` (transitions the agent's
in-flight tasks to ``paused`` and stamps ``last_heartbeat_at``) and then
flips the agent state to IDLE. The closure dispatcher iterates parent
tasks every tick and, for any whose descendants are all terminal, calls
``spawn_agent`` for the closure PM.

If ``i_am_idle``'s pause lands one tick before the dispatcher runs, the
parent's status is already ``paused`` (so it's in the closure dispatcher's
``parent_statuses`` list) but its ``last_heartbeat_at`` is fresh — the
agent literally just heartbeated before pausing itself. Spawning a fresh
container for that PM here would race the in-flight session that just
called i_am_idle.

The gate: skip closure spawn when the task is paused AND
``last_heartbeat_at`` is newer than ``settings.claim_stale_seconds``.
A genuinely-stale paused task (heartbeat older than the cutoff) still
gets the closure spawn — the gate is about *recency*, not paused-ness.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator


def _make_orch() -> AgentOrchestrator:
    """Bypass __init__ — tests don't need a full DI graph."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._claim_heartbeat_ttl = settings.claim_stale_seconds
    return orch


def _paused_parent(*, last_heartbeat_at: datetime | str | None) -> dict[str, Any]:
    """A parent task that's paused and has the requested heartbeat freshness."""
    return {
        "id": str(uuid4()),
        "status": "paused",
        "team": "backend",
        "last_heartbeat_at": last_heartbeat_at,
    }


@pytest.mark.asyncio
async def test_skips_spawn_when_paused_and_recently_touched_datetime() -> None:
    """Recent heartbeat + status=paused = agent just called i_am_idle.

    Last heartbeat is 1 second ago, claim_stale_seconds default is 180s.
    The task is fresh — closure spawn must not fire.
    """
    orch = _make_orch()
    fresh = datetime.now(UTC) - timedelta(seconds=1)
    task = _paused_parent(last_heartbeat_at=fresh)

    client = AsyncMock()

    with (
        patch.object(orch, "_fetch_all_descendants", new=AsyncMock()) as fetch_desc,
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._maybe_spawn_pm_closure(client, task)

    fetch_desc.assert_not_awaited()
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_spawn_when_paused_and_recently_touched_iso_string() -> None:
    """API serializes datetimes as ISO strings; the gate must handle both."""
    orch = _make_orch()
    fresh_iso = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    task = _paused_parent(last_heartbeat_at=fresh_iso)

    client = AsyncMock()

    with (
        patch.object(orch, "_fetch_all_descendants", new=AsyncMock()) as fetch_desc,
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._maybe_spawn_pm_closure(client, task)

    fetch_desc.assert_not_awaited()
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_spawns_when_paused_but_heartbeat_is_stale() -> None:
    """Heartbeat older than claim_stale_seconds = genuinely-stale agent.

    The closure dispatcher should still spawn here — the i_am_idle race
    window has long since closed.
    """
    orch = _make_orch()
    stale = datetime.now(UTC) - timedelta(seconds=settings.claim_stale_seconds + 30)
    task = _paused_parent(last_heartbeat_at=stale)
    descendant = {"id": str(uuid4()), "status": "completed"}

    client = AsyncMock()

    with (
        patch.object(
            orch,
            "_fetch_all_descendants",
            new=AsyncMock(return_value=[descendant]),
        ),
        patch.object(orch, "_already_promoted_for_closure", return_value=False),
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(
            orch,
            "_build_pm_closure_prompt",
            return_value="prompt",
        ),
        patch.object(orch, "_task_git_context", return_value=MagicMock()),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._maybe_spawn_pm_closure(client, task)

    spawn.assert_awaited_once()


@pytest.mark.asyncio
async def test_spawns_when_paused_but_heartbeat_missing() -> None:
    """No heartbeat at all means the freshness gate cannot trigger.

    The legitimate stale-paused-parent case (e.g. a PM was paused before
    Phase 2 added heartbeats) should not be blocked by the new gate.
    """
    orch = _make_orch()
    task = _paused_parent(last_heartbeat_at=None)
    descendant = {"id": str(uuid4()), "status": "completed"}

    client = AsyncMock()

    with (
        patch.object(
            orch,
            "_fetch_all_descendants",
            new=AsyncMock(return_value=[descendant]),
        ),
        patch.object(orch, "_already_promoted_for_closure", return_value=False),
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(
            orch,
            "_build_pm_closure_prompt",
            return_value="prompt",
        ),
        patch.object(orch, "_task_git_context", return_value=MagicMock()),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._maybe_spawn_pm_closure(client, task)

    spawn.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_paused_status_not_gated_by_heartbeat() -> None:
    """Status != paused means the gate is not applicable.

    A claimed/in_progress parent with a fresh heartbeat is normal active
    work and should follow the existing closure logic unchanged.
    """
    orch = _make_orch()
    fresh = datetime.now(UTC) - timedelta(seconds=1)
    task = {
        "id": str(uuid4()),
        "status": "in_progress",
        "team": "backend",
        "last_heartbeat_at": fresh,
    }
    descendant = {"id": str(uuid4()), "status": "completed"}

    client = AsyncMock()

    with (
        patch.object(
            orch,
            "_fetch_all_descendants",
            new=AsyncMock(return_value=[descendant]),
        ),
        patch.object(orch, "_already_promoted_for_closure", return_value=False),
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(
            orch,
            "_build_pm_closure_prompt",
            return_value="prompt",
        ),
        patch.object(orch, "_task_git_context", return_value=MagicMock()),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._maybe_spawn_pm_closure(client, task)

    spawn.assert_awaited_once()

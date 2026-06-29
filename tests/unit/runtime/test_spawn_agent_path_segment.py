"""spawn_agent rejects an unsafe agent_id before any filesystem op.

``agent_id`` flows from request-facing call sites into path building (log
dirs, settings files, container names). A traversal-shaped id (``../evil``,
``a/b``, NUL, empty) must be rejected at the spawn chokepoint — before it
reaches any path join — via ``_safe_agent_path_segment``. The guard is the
first statement in ``spawn_agent`` and only consults a pure static method,
so a bare (un-initialized) orchestrator suffices (same shape as the
human-role guard tests).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator, AgentReadinessError


def _orch() -> Any:
    return object.__new__(AgentOrchestrator)


@pytest.mark.parametrize(
    "bad_id",
    ["../evil", "a/b", "a\\b", ".", "..", "", "be\x00dev"],
)
@pytest.mark.asyncio
async def test_spawn_agent_rejects_unsafe_path_id(bad_id: str) -> None:
    """A traversal-shaped agent_id must raise ValueError before any fs op."""
    orch = _orch()
    with pytest.raises(ValueError, match="unsafe agent id"):
        await orch.spawn_agent(bad_id)


@pytest.mark.asyncio
async def test_spawn_agent_accepts_normal_slug_reaches_later_gate() -> None:
    """A normal slug must NOT trip the path guard — it reaches the downstream
    readiness gate (a *different* refusal), proving the guard doesn't
    over-block the fleet."""
    orch = _orch()

    async def _ready(_aid: str, _tid: str | None) -> str | None:
        return "stubbed-not-ready"

    orch._readiness_gate = _ready
    # Stub any later attribute touch so a bare orch survives past the guard.
    orch._instances = MagicMock()

    with pytest.raises(AgentReadinessError) as exc_info:
        await orch.spawn_agent("be-dev-1", task_id="t-1")

    assert "unsafe agent id" not in str(exc_info.value)
    assert "human-only role" not in str(exc_info.value)

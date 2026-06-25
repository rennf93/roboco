"""Startup re-adoption of still-running agent containers into ``_instances``.

An orchestrator restart loses the in-memory ``_instances`` registry while the
agent containers keep running. The reaper has a Docker-liveness fallback for
that (``_assignee_container_running``), but the spawn gate's ``_is_agent_active``
does not — so after a restart it sees a live agent as inactive and can
double-spawn it onto work its forgotten-but-running container is already doing.
``_readopt_running_agents`` probes each known agent slug's container and
re-registers a minimal ACTIVE instance for any that is running, so both the
reaper's live-skip and the spawn gate see the live agent immediately.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator, AgentState

_EXPECTED_READOPTED = 2


def _orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)  # bypass __init__
    orch._instances = {}
    return orch


@pytest.mark.asyncio
async def test_readopts_running_containers_as_active() -> None:
    orch = _orch()
    running = {"be-dev-1", "fe-pm"}

    async def inspect(name: str) -> tuple[bool, int | None]:
        slug = name.removeprefix("roboco-agent-")
        return (slug in running, 0)

    orch._inspect_container_state = AsyncMock(side_effect=inspect)  # type: ignore[method-assign]

    n = await orch._readopt_running_agents()

    assert n == _EXPECTED_READOPTED
    assert orch._instances["be-dev-1"].state == AgentState.ACTIVE
    assert orch._instances["be-dev-1"].agent_id == "be-dev-1"
    assert orch._instances["fe-pm"].state == AgentState.ACTIVE
    assert "be-dev-2" not in orch._instances  # probed, not running → untracked


@pytest.mark.asyncio
async def test_readopt_leaves_already_tracked_instance_untouched() -> None:
    orch = _orch()
    sentinel = MagicMock()
    orch._instances = {"be-dev-1": sentinel}
    orch._inspect_container_state = AsyncMock(return_value=(True, 0))  # type: ignore[method-assign]

    await orch._readopt_running_agents()

    assert orch._instances["be-dev-1"] is sentinel  # not re-adopted over


@pytest.mark.asyncio
async def test_readopt_inert_when_nothing_running() -> None:
    orch = _orch()
    orch._inspect_container_state = AsyncMock(return_value=(False, None))  # type: ignore[method-assign]

    n = await orch._readopt_running_agents()

    assert n == 0
    assert orch._instances == {}


@pytest.mark.asyncio
async def test_readopt_swallows_probe_errors() -> None:
    orch = _orch()
    orch._inspect_container_state = AsyncMock(side_effect=RuntimeError("no docker"))  # type: ignore[method-assign]

    n = await orch._readopt_running_agents()

    assert n == 0  # best-effort: a probe failure never raises into startup

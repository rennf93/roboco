"""`_write_agent_briefing`'s sandbox availability line.

Names the `request_sandbox` verb for an opted-in project (spec's "name it —
cheap and kills a discovery failure mode" default) and is silent otherwise.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    object.__setattr__(orch, "_TOOL_LOAD_CACHE", {})
    return orch


@pytest.mark.asyncio
async def test_briefing_names_request_sandbox_when_opted_in(tmp_path: object) -> None:
    orch = _orch()
    path = await orch._write_agent_briefing(
        "dev-1", None, str(tmp_path), ["postgres", "redis"]
    )
    assert path is not None
    content = path.read_text()
    assert "request_sandbox()" in content
    assert "postgres, redis" in content


@pytest.mark.asyncio
async def test_briefing_omits_sandbox_line_when_not_opted_in(tmp_path: object) -> None:
    orch = _orch()
    path = await orch._write_agent_briefing("dev-1", None, str(tmp_path), [])
    assert path is not None
    content = path.read_text()
    assert "request_sandbox()" not in content

"""The `playwright` MCP server is gated to fe-qa/ux-qa, plus exactly one
non-QA case: a ux-dev spawned onto a source=video authoring task (probed via
``_is_video_authoring_spawn``), so the composition author can preview their
HTML in a real browser. It must never appear for be-qa (same role, different
team — no chromium in that image) or for a ux-dev outside a video task,
since the binary + wrapper entrypoint are only baked into agent-qa-fe /
agent-ux via docker/agent-qa-fe.Dockerfile / docker/agent-ux.Dockerfile.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    import pytest

_ENTRYPOINT = "/app/scripts/playwright-mcp-entrypoint.sh"


async def _servers_for(agent_slug: str, task_id: str | None = None) -> dict[str, dict]:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    config_path = await orch._generate_mcp_config(agent_slug, task_id=task_id)
    config = json.loads(Path(config_path).read_text())
    servers: dict[str, dict] = config["mcpServers"]
    return servers


async def test_fe_qa_gets_playwright_mcp() -> None:
    servers = await _servers_for("fe-qa")
    assert "playwright" in servers
    assert servers["playwright"]["command"] == _ENTRYPOINT


async def test_ux_qa_gets_playwright_mcp() -> None:
    servers = await _servers_for("ux-qa")
    assert "playwright" in servers
    assert servers["playwright"]["command"] == _ENTRYPOINT


async def test_be_qa_does_not_get_playwright_mcp() -> None:
    """Same `qa` role as fe-qa/ux-qa, but backend team — no chromium baked
    into be-qa's image, so it must not get the MCP registration."""
    servers = await _servers_for("be-qa")
    assert "playwright" not in servers


async def test_ux_dev_does_not_get_playwright_mcp() -> None:
    """Shares agent-ux's image with ux-qa (same Dockerfile, same baked
    browser) but is a `developer` with no video task — must not see the
    tool for ordinary UI work."""
    servers = await _servers_for("ux-dev-1")
    assert "playwright" not in servers


async def test_ux_dev_on_video_task_gets_playwright_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one non-QA case: ux-dev spawned onto a source=video task previews
    the composition in a real browser (the image already bakes chromium)."""

    async def _video(
        _self: AgentOrchestrator, agent_id: str, _agent_role: str, task_id: str | None
    ) -> bool:
        return agent_id == "ux-dev-1" and task_id == "t-video"

    monkeypatch.setattr(AgentOrchestrator, "_is_video_authoring_spawn", _video)
    servers = await _servers_for("ux-dev-1", task_id="t-video")
    assert "playwright" in servers
    assert servers["playwright"]["command"] == _ENTRYPOINT


async def test_video_probe_guards_role_and_team() -> None:
    """Early-outs need no DB: non-developer roles, non-ux teams, and a
    missing task id all refuse before any lookup."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    assert await orch._is_video_authoring_spawn("ux-qa", "qa", "t1") is False
    assert await orch._is_video_authoring_spawn("fe-dev-1", "developer", "t1") is False
    assert await orch._is_video_authoring_spawn("ux-dev-1", "developer", None) is False

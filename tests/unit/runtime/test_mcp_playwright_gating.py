"""The `playwright` MCP server (CEO round-3 note on the Playwright QA-image
work) is role-gated to fe-qa/ux-qa only — it must never appear for be-qa
(same role, different team) or ux-dev (same image as ux-qa, different role),
since the binary + wrapper entrypoint are only baked into agent-qa-fe /
agent-ux via docker/agent-qa-fe.Dockerfile / docker/agent-ux.Dockerfile.
"""

from __future__ import annotations

import json
from pathlib import Path

from roboco.runtime.orchestrator import AgentOrchestrator

_ENTRYPOINT = "/app/scripts/playwright-mcp-entrypoint.sh"


async def _servers_for(agent_slug: str) -> dict[str, dict]:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    config_path = await orch._generate_mcp_config(agent_slug)
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
    browser) but is a `developer`, not `qa` — the gating is role-based, not
    image-based, so ux-dev must not see the tool."""
    servers = await _servers_for("ux-dev-1")
    assert "playwright" not in servers

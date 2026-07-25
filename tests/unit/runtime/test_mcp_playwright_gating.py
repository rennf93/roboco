"""The `playwright` MCP server is gated to fe-qa/ux-qa, plus two non-QA
cases: a ux-dev spawned onto a source=video authoring task (probed via
``_is_video_authoring_spawn``), so the composition author can preview their
HTML in a real browser, and a product_owner spawned onto a source=
board_dogfood task (probed via ``_is_dogfood_spawn``), so the PO can walk
the product as a user (spec: docs/internal/specs/2026-07-24-board-programs-
design.md §4 "Dogfood" — the one program needing more than read tools). It
must never appear for be-qa (same role, different team — no chromium in that
image), for a ux-dev outside a video task, or for a product_owner spawned on
ANY other board program (roadmap/pest_control/scales/etc.) — task-scoped,
not role-blanket. The binary + wrapper entrypoint are baked into agent-qa-fe
/ agent-ux / agent-pm via docker/agent-qa-fe.Dockerfile /
docker/agent-ux.Dockerfile / docker/agent-pm.Dockerfile.
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


async def test_product_owner_on_dogfood_task_gets_playwright_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ONE board-role case: product_owner spawned onto a
    source=board_dogfood task gets the playwright MCP so it can walk the
    product as a user (spec §4)."""

    async def _dogfood(
        _self: AgentOrchestrator, agent_id: str, _agent_role: str, task_id: str | None
    ) -> bool:
        return agent_id == "product-owner" and task_id == "t-dogfood"

    monkeypatch.setattr(AgentOrchestrator, "_is_dogfood_spawn", _dogfood)
    servers = await _servers_for("product-owner", task_id="t-dogfood")
    assert "playwright" in servers
    assert servers["playwright"]["command"] == _ENTRYPOINT


async def test_product_owner_on_other_task_still_refused_when_dogfood_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe is task-scoped: even with a real (monkeypatched) probe
    wired up, a DIFFERENT task id for the same product_owner must not get
    the grant — a roadmap/pest_control/scales spawn must never leak
    browser tools just because Dogfood exists."""

    async def _dogfood(
        _self: AgentOrchestrator, agent_id: str, _agent_role: str, task_id: str | None
    ) -> bool:
        return agent_id == "product-owner" and task_id == "t-dogfood"

    monkeypatch.setattr(AgentOrchestrator, "_is_dogfood_spawn", _dogfood)
    servers = await _servers_for("product-owner", task_id="t-roadmap")
    assert "playwright" not in servers


async def test_head_marketing_does_not_get_playwright_mcp() -> None:
    """Another board role, never gated in for any program."""
    servers = await _servers_for("head-marketing", task_id="t-periscope")
    assert "playwright" not in servers


async def test_dogfood_probe_guards_role_and_task_id() -> None:
    """Early-outs need no DB: a non-product_owner role and a missing task id
    both refuse before any lookup — mirrors
    ``test_video_probe_guards_role_and_team``."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    assert await orch._is_dogfood_spawn("main-pm", "main_pm", "t1") is False
    assert await orch._is_dogfood_spawn("head-marketing", "head_marketing", "t1") is (
        False
    )
    assert await orch._is_dogfood_spawn("product-owner", "product_owner", None) is False

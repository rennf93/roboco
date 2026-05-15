"""Smoke-8: briefing's _build_tool_load_block emits the ToolSearch directive
without depending on a "## Load on spawn" section in the role file.

The previous implementation scraped role prompts for that marker; since
no role file had it, the function returned "" and the briefing showed
agents no tool-load instruction. Combined with weak models skipping the
system-prompt directive, the agent went straight to Edit and hit "not
enabled in this context."

Fix: per-role tool list lives in the orchestrator (mirrors the
factories layer). Pre-renders the directive directly — no file scrape.
"""

from __future__ import annotations

import re
from unittest.mock import patch

from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._TOOL_LOAD_CACHE = {}
    return orch


def test_developer_directive_includes_edit_and_write() -> None:
    block = _orch()._build_tool_load_block("developer")
    assert "First action required" in block
    assert "ToolSearch" in block
    assert "Edit" in block
    assert "Write" in block


def test_documenter_directive_includes_edit_and_write() -> None:
    block = _orch()._build_tool_load_block("documenter")
    assert "Edit" in block
    assert "Write" in block


def test_qa_directive_excludes_edit_and_write() -> None:
    block = _orch()._build_tool_load_block("qa")
    assert "First action required" in block
    # The ToolSearch line itself: pull the comma list to be precise about
    # which tools are listed (substring match would catch "TodoWrite").
    match = re.search(r'select:([^"]+)"', block)
    assert match is not None
    tools = match.group(1).split(",")
    assert "Edit" not in tools
    assert "Write" not in tools
    assert "Read" in tools
    assert "Bash" in tools


def test_pm_directives_exclude_edit_and_write() -> None:
    for role in ("main_pm", "cell_pm", "product_owner", "head_marketing", "auditor"):
        block = _orch()._build_tool_load_block(role)
        match = re.search(r'select:([^"]+)"', block)
        assert match is not None
        tools = match.group(1).split(",")
        assert "Edit" not in tools, f"{role} must not list Edit"
        assert "Write" not in tools, f"{role} must not list Write"


def test_unknown_role_returns_empty() -> None:
    """No directive for unknown roles (defensive)."""
    block = _orch()._build_tool_load_block("nonexistent")
    assert block == ""


def test_directive_warns_about_failure_mode() -> None:
    block = _orch()._build_tool_load_block("developer")
    assert "Edit exists but is not enabled" in block


def test_role_cache_works() -> None:
    orch = _orch()
    first = orch._build_tool_load_block("developer")
    # Second call hits the cache (same return).
    second = orch._build_tool_load_block("developer")
    assert first is second  # same string object

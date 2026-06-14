"""#167: the briefing's _build_tool_load_block must not push ToolSearch.

Earlier (smoke-8) the block instructed agents to run a ToolSearch call
to "activate deferred built-in tools". That premise was false —
ToolSearch is MCP-only, never gates built-ins, and is not callable in
the agent runtime — so weak models chased a nonexistent tool and fell
back to destructive shell file-writes. The real cause of "Edit not
enabled in this context" was a permission bug fixed separately.

The block now affirms the role's built-in tools are loaded and ready,
tells the agent NOT to call ToolSearch, and (for authoring roles)
steers away from whole-file shell redirection.
"""

from __future__ import annotations

from unittest.mock import patch

from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    object.__setattr__(orch, "_TOOL_LOAD_CACHE", {})
    return orch


def _tool_names(block: str) -> list[str]:
    """Exact tool tokens (so 'TodoWrite' is not mistaken for 'Write')."""
    line = next(ln for ln in block.splitlines() if "available now:" in ln)
    seg = line.split("available now: ", 1)[1]
    return seg.split(".", 1)[0].split(", ")


def test_developer_block_affirms_tools_no_toolsearch_call() -> None:
    block = _orch()._build_tool_load_block("developer")
    assert "Your tools are ready" in block
    assert "ToolSearch(query=" not in block
    assert "are deferred" not in block
    names = _tool_names(block)
    assert "Edit" in names and "Write" in names


def test_developer_block_steers_away_from_shell_redirection() -> None:
    block = _orch()._build_tool_load_block("developer")
    assert "shell redirection" in block
    assert "Edit/Write" in block


def test_documenter_block_lists_edit_and_write() -> None:
    names = _tool_names(_orch()._build_tool_load_block("documenter"))
    assert "Edit" in names and "Write" in names


def test_qa_block_excludes_edit_and_write() -> None:
    block = _orch()._build_tool_load_block("qa")
    assert "Your tools are ready" in block
    names = _tool_names(block)
    assert "Edit" not in names and "Write" not in names
    assert "Read" in names and "Bash" in names


def test_pm_blocks_exclude_edit_and_write() -> None:
    for role in ("main_pm", "cell_pm", "product_owner", "head_marketing", "auditor"):
        names = _tool_names(_orch()._build_tool_load_block(role))
        assert "Edit" not in names, f"{role} must not list Edit"
        assert "Write" not in names, f"{role} must not list Write"


def test_no_role_block_lists_the_task_subagent_tool() -> None:
    """Task (sub-agent dispatch) is dropped from the briefing tool grant.

    No role uses Task and there are no custom sub-agent definitions, so it only
    spawns a context-blind generic sub-agent that burns budget.
    """
    for role in (
        "developer",
        "documenter",
        "qa",
        "main_pm",
        "cell_pm",
        "product_owner",
        "head_marketing",
        "auditor",
    ):
        names = _tool_names(_orch()._build_tool_load_block(role))
        assert "Task" not in names, f"{role} must not list Task: {names}"


def test_unknown_role_returns_empty() -> None:
    assert _orch()._build_tool_load_block("nonexistent") == ""


def test_role_cache_works() -> None:
    orch = _orch()
    first = orch._build_tool_load_block("developer")
    second = orch._build_tool_load_block("developer")
    assert first is second

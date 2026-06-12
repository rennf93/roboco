"""#167: the system prompt must NOT tell agents to ToolSearch built-ins.

Earlier (smoke-7) the prompt opened with a "# FIRST ACTION REQUIRED:
run ToolSearch to activate Edit/Write" block. That premise was false:
ToolSearch is MCP-only and never gates built-in tools, and it is not
even a callable tool in the agent runtime. Weak models chased the
nonexistent tool, concluded Edit/Write were unavailable, and rewrote
whole files via destructive shell redirection. The real cause of
"Edit exists but is not enabled in this context" was a permission bug
(global Write(*)/Edit(*) deny + single-slash path), fixed separately.

The directive layer now affirms the tools are loaded and ready, tells
agents NOT to call ToolSearch, and (for authoring roles) steers away
from whole-file shell redirection.
"""

from __future__ import annotations

from roboco.agents.factories._base import compose_prompt
from roboco.models import AgentRole, Team


def _composed_prompt_for(role: AgentRole, team: Team | None = None) -> str:
    return compose_prompt(role, team, agent_slug="test-agent")


def test_prompt_no_longer_instructs_a_toolsearch_call() -> None:
    """No role prompt may instruct an actual ToolSearch(query=...) call."""
    for role in (
        AgentRole.DEVELOPER,
        AgentRole.DOCUMENTER,
        AgentRole.QA,
        AgentRole.MAIN_PM,
        AgentRole.CELL_PM,
    ):
        prompt = _composed_prompt_for(role, Team.BACKEND)
        assert "ToolSearch(query=" not in prompt, (
            f"{role.value} prompt still instructs a ToolSearch call"
        )
        assert "are deferred" not in prompt, (
            f"{role.value} prompt still claims built-ins are deferred"
        )


def test_developer_prompt_starts_with_tools_ready_block() -> None:
    """Developer system prompt leads with the tools-ready affirmation."""
    prompt = _composed_prompt_for(AgentRole.DEVELOPER, Team.BACKEND)
    assert prompt.startswith("# Your tools are ready"), (
        f"Developer prompt must lead with the tools-ready block. "
        f"Got first 80 chars: {prompt[:80]!r}"
    )


def _tool_names(prompt: str) -> list[str]:
    """Exact tool tokens from the 'available now: <names>.' enumeration.

    Exact tokens matter: a substring check would treat 'TodoWrite' as
    containing 'Write'.
    """
    line = next(ln for ln in prompt.splitlines() if "available now:" in ln)
    seg = line.split("available now: ", 1)[1]
    # _base layer ends the list with '.'; orchestrator continues with
    # '. Use them...'. Either way the names stop at the first period.
    return seg.split(".", 1)[0].split(", ")


def test_developer_block_lists_edit_and_write_as_available() -> None:
    """Authoring roles are told Edit + Write are loaded and available."""
    for role in (AgentRole.DEVELOPER, AgentRole.DOCUMENTER):
        names = _tool_names(_composed_prompt_for(role, Team.BACKEND))
        assert "Edit" in names and "Write" in names, (
            f"{role.value} tools-ready line must list Edit + Write: {names!r}"
        )


def test_developer_block_steers_away_from_shell_redirection() -> None:
    """The exact failure mode (clobber a file via bash) is called out."""
    prompt = _composed_prompt_for(AgentRole.DEVELOPER, Team.BACKEND)
    assert "shell redirection" in prompt
    assert "Edit/Write" in prompt


def test_qa_block_excludes_edit_and_write() -> None:
    """QA reads/reviews — Edit/Write must not be listed as available."""
    names = _tool_names(_composed_prompt_for(AgentRole.QA, Team.BACKEND))
    assert "Edit" not in names and "Write" not in names, names
    assert "Read" in names and "Bash" in names


def test_pm_blocks_exclude_edit_and_write() -> None:
    for role in (AgentRole.MAIN_PM, AgentRole.CELL_PM):
        names = _tool_names(_composed_prompt_for(role))
        assert "Edit" not in names and "Write" not in names, (
            f"{role.value} must not list Edit/Write: {names}"
        )


def test_no_role_is_granted_the_task_subagent_tool() -> None:
    """Task (sub-agent dispatch) is dropped from the built-in tool grant.

    No role prompt or workflow uses Task and there are no custom sub-agent
    definitions, so a Task call only spawns a context-blind generic sub-agent
    that burns budget. The tools-ready line must not advertise it for any role.
    """
    for role in (
        AgentRole.DEVELOPER,
        AgentRole.DOCUMENTER,
        AgentRole.QA,
        AgentRole.MAIN_PM,
        AgentRole.CELL_PM,
    ):
        names = _tool_names(_composed_prompt_for(role, Team.BACKEND))
        assert "Task" not in names, f"{role.value} must not list Task: {names}"


def test_block_is_first_layer_before_lifecycle() -> None:
    """Tools-ready block precedes the lifecycle and base layers."""
    prompt = _composed_prompt_for(AgentRole.DEVELOPER, Team.BACKEND)
    first_idx = prompt.find("# Your tools are ready")
    lifecycle_idx = prompt.find("Lifecycle")
    base_idx = prompt.find("RoboCo Agent — Base")
    assert first_idx == 0
    if lifecycle_idx > -1:
        assert first_idx < lifecycle_idx
    if base_idx > -1:
        assert first_idx < base_idx

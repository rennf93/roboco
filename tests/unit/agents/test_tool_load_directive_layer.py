"""Smoke-7: system prompt opens with a ToolSearch directive that activates
deferred built-in tools (Edit, Write, Read, ...).

Original bug: be-dev-1 called Edit and got "Edit exists but is not enabled
in this context" because Claude Code v2.1.69+ defers built-in tools behind
a ToolSearch call. The role prompts said "no ToolSearch needed" — a lie
for built-in tools — so weak models skipped the activation step.

Fix: compose_prompt now prepends a tool-load directive layer that names
the exact ToolSearch call for the role. It's the highest-priority block in
the system prompt so even weak models follow it.
"""

from __future__ import annotations

from roboco.agents.factories._base import compose_prompt
from roboco.models import AgentRole, Team


def _composed_prompt_for(role: AgentRole, team: Team | None = None) -> str:
    """Compose the prompt for a role and team."""
    return compose_prompt(role, team, agent_slug="test-agent")


def test_developer_prompt_starts_with_tool_load_directive() -> None:
    """Developer system prompt begins with the ToolSearch activation block."""
    prompt = _composed_prompt_for(AgentRole.DEVELOPER, Team.BACKEND)
    assert prompt.startswith("# FIRST ACTION REQUIRED"), (
        f"Developer prompt must lead with the tool-load directive. "
        f"Got first 80 chars: {prompt[:80]!r}"
    )


def test_developer_directive_names_edit_and_write() -> None:
    """Developer ToolSearch call lists Edit + Write (the smoke-7 wedge)."""
    prompt = _composed_prompt_for(AgentRole.DEVELOPER, Team.BACKEND)
    assert 'ToolSearch(query="select:' in prompt
    # Find the ToolSearch line
    line = next(line for line in prompt.splitlines() if "ToolSearch(query=" in line)
    assert "Edit" in line, f"Developer ToolSearch missing Edit: {line!r}"
    assert "Write" in line


def test_documenter_directive_names_edit_and_write() -> None:
    """Documenter needs Edit/Write too — they author docs."""
    prompt = _composed_prompt_for(AgentRole.DOCUMENTER, Team.BACKEND)
    line = next(line for line in prompt.splitlines() if "ToolSearch(query=" in line)
    assert "Edit" in line
    assert "Write" in line


def _tool_list_from_directive(prompt: str) -> list[str]:
    """Extract the comma-separated tool list from the ToolSearch call."""
    line = next(line for line in prompt.splitlines() if "ToolSearch(query=" in line)
    # `ToolSearch(query="select:Read,Bash,...")` — pull out the names.
    start = line.find("select:") + len("select:")
    end = line.find('"', start)
    return line[start:end].split(",")


def test_qa_directive_excludes_edit_and_write() -> None:
    """QA reads but doesn't author — directive must NOT activate Edit/Write."""
    tools = _tool_list_from_directive(_composed_prompt_for(AgentRole.QA, Team.BACKEND))
    assert "Edit" not in tools, f"QA must not activate Edit: tools={tools}"
    assert "Write" not in tools
    assert "Read" in tools
    assert "Bash" in tools


def test_pm_directives_exclude_edit_and_write() -> None:
    """PMs coordinate; they don't author code. No Edit/Write activation."""
    for role in (AgentRole.MAIN_PM, AgentRole.CELL_PM):
        tools = _tool_list_from_directive(_composed_prompt_for(role))
        assert "Edit" not in tools, f"{role.value} must not activate Edit: {tools}"
        assert "Write" not in tools


def test_directive_explains_why_it_matters() -> None:
    """The block must mention 'Edit exists but is not enabled' so the agent
    understands what skipping the call causes."""
    prompt = _composed_prompt_for(AgentRole.DEVELOPER, Team.BACKEND)
    assert "Edit exists but is not enabled" in prompt


def test_directive_is_first_layer_before_lifecycle() -> None:
    """First Action block precedes the lifecycle and base layers."""
    prompt = _composed_prompt_for(AgentRole.DEVELOPER, Team.BACKEND)
    first_idx = prompt.find("# FIRST ACTION REQUIRED")
    lifecycle_idx = prompt.find("Lifecycle")
    base_idx = prompt.find("RoboCo Agent — Base")
    assert first_idx == 0
    if lifecycle_idx > -1:
        assert first_idx < lifecycle_idx
    if base_idx > -1:
        assert first_idx < base_idx

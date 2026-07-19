"""compose_prompt includes the Task history guidance section for the intake role."""

from __future__ import annotations

from roboco.agents.factories._base import compose_prompt
from roboco.models import AgentRole


def test_task_history_guidance_present_for_prompter() -> None:
    prompt = compose_prompt(AgentRole.PROMPTER, None, "intake-1")
    assert "## Task history" in prompt


def test_task_history_guidance_names_the_search_tool_and_duplicate_avoidance() -> None:
    """The static guidance names the ambient heading, the search tool, and the
    three explicit uses the CEO asked for: avoid duplicates, cite precedent by
    short id, and let history inform sequencing."""
    prompt = compose_prompt(AgentRole.PROMPTER, None, "intake-1")
    assert "search_past_tasks" in prompt
    assert "Avoid duplicates" in prompt
    assert "Cite precedent" in prompt
    assert "Sequence with judgment" in prompt

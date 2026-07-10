"""The SDK-driver agents (intake, secretary) hard-disallow the `Task` tool.

`Task` is a default-permitted Claude Code built-in: omitting it from
`allowed_tools` only removes an auto-approve entry, it does not restrict, and
`permission_mode="dontAsk"` never routes a pre-permitted built-in through the
`can_use_tool` gate. So the ONLY claude-code-level block is an explicit
`disallowed_tools=["Task"]` (→ CLI `--disallowedTools Task`). These pin that
the intake interviewer and the Secretary cannot fan out subagents.
"""

from __future__ import annotations

from roboco.agent_sdk.intake_driver import build_intake_options
from roboco.agent_sdk.secretary_driver import build_secretary_options


def test_intake_options_disallow_task() -> None:
    opts = build_intake_options(
        system_prompt="x", cwd="/tmp", session_id="s1", model="sonnet"
    )
    assert "Task" in opts.disallowed_tools
    assert "Task" not in opts.allowed_tools


def test_secretary_options_disallow_task() -> None:
    opts = build_secretary_options(system_prompt="x", cwd="/tmp", model="sonnet")
    assert "Task" in opts.disallowed_tools
    assert "Task" not in opts.allowed_tools

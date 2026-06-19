"""grok_cli_config — mcp-config → config.toml + per-role grok CLI flags."""

from __future__ import annotations

import json
import tomllib
from typing import TYPE_CHECKING

from roboco.llm.providers import grok_cli_config as gc

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_SAMPLE_MCP = {
    "mcpServers": {
        "roboco-flow": {
            "command": "uv",
            "args": ["run", "--no-sync", "python", "-m", "roboco.mcp.flow_server"],
            "env": {"ROBOCO_AGENT_ID": "be-dev-1", "ROBOCO_AGENT_TOKEN": "tok-123"},
        },
        "roboco-do": {"command": "uv", "args": ["run", "x"]},
    }
}


def _disallowed(args: list[str]) -> str:
    return args[args.index("--disallowed-tools") + 1]


def test_render_config_toml_is_valid_toml_and_injects_env() -> None:
    parsed = tomllib.loads(gc.render_config_toml(_SAMPLE_MCP))
    flow = parsed["mcp_servers"]["roboco-flow"]
    assert flow["command"] == "uv"
    assert flow["args"][:2] == ["run", "--no-sync"]
    # The gateway env (token) reaches the server block — the keystone.
    assert flow["env"]["ROBOCO_AGENT_TOKEN"] == "tok-123"
    # A server with no env omits the key entirely.
    assert "env" not in parsed["mcp_servers"]["roboco-do"]


def test_render_config_toml_empty_when_no_servers() -> None:
    assert gc.render_config_toml({}) == ""
    assert gc.render_config_toml({"mcpServers": {}}) == ""


def test_write_agents_md_installs_the_blueprint(tmp_path: Path) -> None:
    src = tmp_path / "system-prompt.md"
    src.write_text("You are the RoboCo intake interviewer.", encoding="utf-8")
    dest = tmp_path / ".grok" / "AGENTS.md"
    assert gc.write_agents_md(source=src, dest=dest) is True
    assert dest.read_text(encoding="utf-8") == "You are the RoboCo intake interviewer."


def test_write_agents_md_noops_when_source_absent(tmp_path: Path) -> None:
    dest = tmp_path / ".grok" / "AGENTS.md"
    assert gc.write_agents_md(source=tmp_path / "absent.md", dest=dest) is False
    assert not dest.exists()


def test_developer_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBOCO_GROK_REASONING_EFFORT", raising=False)
    args = gc.grok_cli_args("be-dev-1")
    dis = _disallowed(args)
    # Developer writes code + runs a shell → only subagents removed.
    assert "Agent" in dis
    assert "run_terminal_cmd" not in dis
    assert "search_replace" not in dis
    # Raw git mutation + destructive ops denied even with a shell.
    assert "--deny" in args
    assert "Bash(git push*)" in args
    assert "Bash(rm -rf*)" in args
    # Code-quality role keeps full reasoning.
    assert "--effort" not in args


def test_pr_reviewer_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBOCO_GROK_REASONING_EFFORT", raising=False)
    args = gc.grok_cli_args("pr-reviewer-1")
    dis = _disallowed(args)
    assert "run_terminal_cmd" in dis  # read-only reviewer: no shell
    assert "search_replace" in dis  # no editing
    assert "--deny" not in args  # bash removed → no command rules left
    assert "--effort" not in args  # code-quality reviewer keeps full reasoning


def test_main_pm_keeps_shell_but_denies_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBOCO_GROK_REASONING_EFFORT", raising=False)
    args = gc.grok_cli_args("main-pm")
    dis = _disallowed(args)
    assert "run_terminal_cmd" not in dis  # PM keeps a shell
    assert "search_replace" in dis  # but does not write code
    assert "Bash(git push*)" in args  # raw git mutation denied
    # Parity with Claude: model-default reasoning for every role, no per-role cut.
    assert "--effort" not in args


def test_prompter_allows_subagents_but_no_shell_or_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ROBOCO_GROK_REASONING_EFFORT", raising=False)
    dis = _disallowed(gc.grok_cli_args_for_role("prompter"))
    # The intake interviewer may fan out to subagents (parity with Claude's Task)…
    assert "Agent" not in dis
    # …but it is still a read-only conversational role: no shell, no editing.
    assert "run_terminal_cmd" in dis
    assert "search_replace" in dis


def test_web_search_disabled_for_every_role() -> None:
    for role in ("developer", "prompter", "secretary", "main_pm", "pr_reviewer"):
        assert "--disable-web-search" in gc.grok_cli_args_for_role(role)


def test_effort_is_fleet_override_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_GROK_REASONING_EFFORT", "high")
    args = gc.grok_cli_args("be-dev-1")
    assert args[args.index("--effort") + 1] == "high"
    # "full" / "default" / empty keep grok's model default (no --effort).
    monkeypatch.setenv("ROBOCO_GROK_REASONING_EFFORT", "full")
    assert "--effort" not in gc.grok_cli_args("main-pm")
    monkeypatch.delenv("ROBOCO_GROK_REASONING_EFFORT", raising=False)
    assert "--effort" not in gc.grok_cli_args("documenter")


def test_max_turns_is_emitted() -> None:
    args = gc.grok_cli_args("be-dev-1", max_turns=7)
    assert args[args.index("--max-turns") + 1] == "7"


def test_bash_roles_deny_the_full_git_mutation_set() -> None:
    # Graceful native --deny rules (the agent recovers) covering the same git
    # network / branch / history ops the Claude bash-guard blocks.
    args = gc.grok_cli_args_for_role("developer")
    for op in ("push", "fetch", "clone", "checkout", "merge", "rebase", "revert"):
        assert f"Bash(git {op}*)" in args
    assert "Bash(rm -rf*)" in args


def test_bash_guard_hook_config_skips_git() -> None:
    handler = gc.bash_guard_hook_config("/app/scripts/bash-guard-hook.sh")[
        "hooks"
    ]["PreToolUse"][0]
    assert handler["matcher"] == "Bash"
    inner = handler["hooks"][0]
    assert inner["command"] == "/app/scripts/bash-guard-hook.sh"
    # Git is handled by graceful --deny, so the hook skips it (exfil only).
    assert inner["env"]["ROBOCO_GUARD_SKIP_GIT"] == "1"


def test_write_grok_hooks_installs_when_script_present(tmp_path: Path) -> None:
    script = tmp_path / "bash-guard-hook.sh"
    script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    hooks_dir = tmp_path / ".grok" / "hooks"
    assert gc.write_grok_hooks(hooks_dir=hooks_dir, hook_path=str(script)) is True
    written = json.loads((hooks_dir / "roboco-bash-guard.json").read_text())
    assert written["hooks"]["PreToolUse"][0]["matcher"] == "Bash"


def test_write_grok_hooks_noops_when_script_absent(tmp_path: Path) -> None:
    hooks_dir = tmp_path / ".grok" / "hooks"
    assert (
        gc.write_grok_hooks(hooks_dir=hooks_dir, hook_path=str(tmp_path / "nope.sh"))
        is False
    )
    assert not hooks_dir.exists()

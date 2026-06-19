"""grok_cli_config — mcp-config → config.toml + per-role grok CLI flags."""

from __future__ import annotations

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
    assert args[args.index("--effort") + 1] == "low"


def test_effort_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_GROK_REASONING_EFFORT", "high")
    assert (
        gc.grok_cli_args("be-dev-1")[gc.grok_cli_args("be-dev-1").index("--effort") + 1]
        == "high"
    )
    # "full" disables the per-role reduction entirely.
    monkeypatch.setenv("ROBOCO_GROK_REASONING_EFFORT", "full")
    assert "--effort" not in gc.grok_cli_args("main-pm")


def test_max_turns_is_emitted() -> None:
    args = gc.grok_cli_args("be-dev-1", max_turns=7)
    assert args[args.index("--max-turns") + 1] == "7"

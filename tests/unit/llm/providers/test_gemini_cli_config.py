"""gemini_cli_config — mcp-config -> settings.json + per-role Policy Engine TOML."""

from __future__ import annotations

import json
import tomllib
from typing import TYPE_CHECKING

from roboco.llm.providers import gemini_cli_config as gc

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


def _rules_by_tool(rules: list[dict], tool: str) -> list[dict]:
    return [r for r in rules if r.get("toolName") == tool]


def test_render_settings_json_injects_mcp_servers_and_env() -> None:
    rendered = gc.render_settings_json(_SAMPLE_MCP)
    flow = rendered["mcpServers"]["roboco-flow"]
    assert flow["command"] == "uv"
    assert flow["args"][:2] == ["run", "--no-sync"]
    assert flow["env"]["ROBOCO_AGENT_TOKEN"] == "tok-123"
    assert "env" not in rendered["mcpServers"]["roboco-do"]


def test_render_settings_json_fixed_flags() -> None:
    rendered = gc.render_settings_json({})
    assert rendered["security"]["auth"]["selectedType"] == "oauth-personal"
    assert rendered["experimental"]["enableAgents"] is False
    assert rendered["advanced"]["autoConfigureMemory"] is False
    assert rendered["mcpServers"] == {}


def test_write_gemini_memory_installs_the_blueprint(tmp_path: Path) -> None:
    src = tmp_path / "system-prompt.md"
    src.write_text("You are a RoboCo backend developer.", encoding="utf-8")
    dest = tmp_path / ".gemini" / "GEMINI.md"
    assert gc.write_gemini_memory(source=src, dest=dest) is True
    assert dest.read_text(encoding="utf-8") == "You are a RoboCo backend developer."


def test_write_gemini_memory_noops_when_source_absent(tmp_path: Path) -> None:
    dest = tmp_path / ".gemini" / "GEMINI.md"
    assert gc.write_gemini_memory(source=tmp_path / "absent.md", dest=dest) is False
    assert not dest.exists()


def test_developer_policy_only_denies_bash_capable_hazards() -> None:
    rules = gc.policy_rules_for_role("developer")
    # Developer writes code + runs a shell -> no edit-tool / shell-blanket deny.
    assert _rules_by_tool(rules, "write_file") == []
    assert _rules_by_tool(rules, "replace") == []
    shell_rules = _rules_by_tool(rules, "run_shell_command")
    assert shell_rules  # bash-capable: command-scoped denies exist
    assert all("commandPrefix" in r for r in shell_rules)
    prefixes = {r["commandPrefix"] for r in shell_rules}
    assert "git push" in prefixes
    assert "rm -rf" in prefixes


def test_pr_reviewer_policy_blanket_denies_shell_and_edit() -> None:
    rules = gc.policy_rules_for_role("pr_reviewer")
    assert _rules_by_tool(rules, "write_file")
    assert _rules_by_tool(rules, "replace")
    shell_rules = _rules_by_tool(rules, "run_shell_command")
    # A read-only reviewer gets ONE blanket shell deny, no command scoping.
    assert len(shell_rules) == 1
    assert "commandPrefix" not in shell_rules[0]


def test_main_pm_keeps_shell_but_denies_git_and_edit() -> None:
    rules = gc.policy_rules_for_role("main_pm")
    assert _rules_by_tool(rules, "write_file")  # PM doesn't write code
    shell_rules = _rules_by_tool(rules, "run_shell_command")
    prefixes = {r.get("commandPrefix") for r in shell_rules}
    assert "git push" in prefixes
    assert None not in prefixes  # no blanket deny — PM keeps its shell


def test_render_policy_toml_is_valid_toml() -> None:
    parsed = tomllib.loads(gc.render_policy_toml("developer"))
    assert isinstance(parsed["rule"], list)
    assert all(r["decision"] == "deny" for r in parsed["rule"])


def test_render_policy_toml_empty_when_no_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every REAL role currently produces at least one rule (write or shell
    # denies), but render_policy_toml must still degrade to "" rather than
    # emit an empty [[rule]] table for the hypothetical case it doesn't.
    monkeypatch.setattr(gc, "policy_rules_for_role", lambda _role: [])
    assert gc.render_policy_toml("anything") == ""


def test_unknown_role_gets_every_deny_category() -> None:
    # An unrecognised role name fails _allows_write's role_config lookup (->
    # False) and isn't in _BASH_ROLES, so it gets edit denies PLUS the blanket
    # shell deny — the most restrictive combination.
    rules = gc.policy_rules_for_role("unknown-role-xyz")
    assert _rules_by_tool(rules, "write_file")
    assert _rules_by_tool(rules, "replace")
    assert len(_rules_by_tool(rules, "run_shell_command")) == 1


def test_write_policy_toml_writes_file(tmp_path: Path) -> None:
    policies_dir = tmp_path / "policies"
    assert gc.write_policy_toml("developer", policies_dir=policies_dir) is True
    written = (policies_dir / "roboco.toml").read_text(encoding="utf-8")
    assert "run_shell_command" in written


def test_gemini_cli_args_is_yolo_only() -> None:
    assert gc.gemini_cli_args() == ["--approval-mode", "yolo"]


def test_main_writes_settings_and_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_path = tmp_path / "mcp-config.json"
    mcp_path.write_text(json.dumps(_SAMPLE_MCP), encoding="utf-8")
    settings_path = tmp_path / ".gemini" / "settings.json"
    memory_path = tmp_path / ".gemini" / "GEMINI.md"
    policies_dir = tmp_path / ".gemini" / "policies"
    args_path = tmp_path / "gemini-args"
    system_prompt = tmp_path / "system-prompt.md"
    system_prompt.write_text("blueprint", encoding="utf-8")

    monkeypatch.setattr(gc, "GEMINI_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(gc, "GEMINI_MEMORY_PATH", memory_path)
    monkeypatch.setattr(gc, "GEMINI_POLICIES_DIR", policies_dir)
    monkeypatch.setattr(gc, "GEMINI_ARGS_PATH", args_path)
    monkeypatch.setattr(gc, "SYSTEM_PROMPT_PATH", system_prompt)
    monkeypatch.setenv("ROBOCO_AGENT_ID", "be-dev-1")
    monkeypatch.setenv("ROBOCO_MCP_CONFIG", str(mcp_path))

    assert gc.main() == 0

    rendered = json.loads(settings_path.read_text(encoding="utf-8"))
    assert rendered["mcpServers"]["roboco-flow"]["env"]["ROBOCO_AGENT_TOKEN"] == (
        "tok-123"
    )
    assert memory_path.read_text(encoding="utf-8") == "blueprint"
    assert (policies_dir / "roboco.toml").exists()
    # One flag token per line — the entrypoint reads it via bash `mapfile -t`.
    assert args_path.read_text(encoding="utf-8").splitlines() == [
        "--approval-mode",
        "yolo",
    ]

"""codex_cli_config — mcp-config → config.toml + execpolicy rules + combined
prompt + per-role sandbox flag."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

from roboco.llm.providers import codex_cli_config as cc

if TYPE_CHECKING:
    from pathlib import Path

_SAMPLE_MCP = {
    "mcpServers": {
        "roboco-flow": {
            "command": "uv",
            "args": ["run", "--no-sync", "python", "-m", "roboco.mcp.flow_server"],
            "env": {"ROBOCO_AGENT_ID": "be-dev-1", "ROBOCO_AGENT_TOKEN": "tok-123"},
        },
        "roboco-do": {"command": "uv", "args": ["run", "x"]},
        "roboco-optimal": {"command": "uv", "args": ["run", "y"]},
    }
}


def test_render_config_toml_is_valid_toml_and_injects_env() -> None:
    parsed = tomllib.loads(cc.render_config_toml(_SAMPLE_MCP))
    flow = parsed["mcp_servers"]["roboco-flow"]
    assert flow["command"] == "uv"
    assert flow["args"][:2] == ["run", "--no-sync"]
    assert flow["env"]["ROBOCO_AGENT_TOKEN"] == "tok-123"
    assert "env" not in parsed["mcp_servers"]["roboco-do"]


def test_render_config_toml_marks_gateway_pair_required() -> None:
    parsed = tomllib.loads(cc.render_config_toml(_SAMPLE_MCP))
    assert parsed["mcp_servers"]["roboco-flow"]["required"] is True
    assert parsed["mcp_servers"]["roboco-do"]["required"] is True
    # Every other server is best-effort — no `required` key at all.
    assert "required" not in parsed["mcp_servers"]["roboco-optimal"]


def test_render_config_toml_empty_when_no_servers() -> None:
    assert cc.render_config_toml({}) == ""
    assert cc.render_config_toml({"mcpServers": {}}) == ""


def test_sandbox_level_developer_is_workspace_write() -> None:
    assert cc.sandbox_level_for_role("developer") == "workspace-write"


def test_sandbox_level_other_delivery_roles_are_read_only() -> None:
    # Narrower than grok's per-role allows_write (documenter also writes there)
    # — Codex V1 restricts local sandbox writes to developer only; documenter's
    # real writes ride the roboco-docs MCP server, not a local file edit.
    for role in ("qa", "documenter", "pr_reviewer", "cell_pm", "main_pm", ""):
        assert cc.sandbox_level_for_role(role) == "read-only"


def test_codex_cli_args_for_role_carries_sandbox_and_skip_git_check() -> None:
    dev_args = cc.codex_cli_args_for_role("developer")
    assert dev_args == ["--sandbox", "workspace-write", "--skip-git-repo-check"]
    qa_args = cc.codex_cli_args_for_role("qa")
    assert qa_args == ["--sandbox", "read-only", "--skip-git-repo-check"]


def test_render_execpolicy_rules_covers_git_mutation_destructive_and_raw_pm() -> None:
    rules = cc.render_execpolicy_rules()
    assert 'prefix_rule(pattern = ["git", "push"], decision = "forbidden")' in rules
    assert 'prefix_rule(pattern = ["git", "tag", "-d"], decision = "forbidden")' in (
        rules
    )
    assert 'prefix_rule(pattern = ["rm", "-rf"], decision = "forbidden")' in rules
    assert 'prefix_rule(pattern = ["uv", "run"], decision = "forbidden")' in rules
    assert 'prefix_rule(pattern = ["pip", "install"], decision = "forbidden")' in rules
    # Only allow/forbidden decisions — never `prompt` (blocks headless turns).
    assert "prompt" not in rules


def test_write_execpolicy_rules_writes_to_dest(tmp_path: Path) -> None:
    dest = tmp_path / "rules" / "default.rules"
    cc.write_execpolicy_rules(dest=dest)
    assert dest.exists()
    assert "git" in dest.read_text(encoding="utf-8")


def test_render_combined_prompt_joins_system_and_task() -> None:
    combined = cc.render_combined_prompt("You are the developer.", "Fix the bug.")
    assert combined.startswith("You are the developer.")
    assert combined.endswith("Fix the bug.")
    assert "---" in combined


def test_render_combined_prompt_degrades_gracefully() -> None:
    assert cc.render_combined_prompt("", "task only") == "task only"
    assert cc.render_combined_prompt("system only", "") == "system only"
    assert cc.render_combined_prompt("", "") == ""


def test_write_combined_prompt_reads_source_and_writes_dest(tmp_path: Path) -> None:
    src = tmp_path / "system-prompt.md"
    src.write_text("You are the RoboCo developer.", encoding="utf-8")
    dest = tmp_path / "prompt.txt"
    found = cc.write_combined_prompt(
        task_prompt="Implement the feature.", source=src, dest=dest
    )
    assert found is True
    text = dest.read_text(encoding="utf-8")
    assert "You are the RoboCo developer." in text
    assert "Implement the feature." in text


def test_write_combined_prompt_degrades_when_source_absent(tmp_path: Path) -> None:
    dest = tmp_path / "prompt.txt"
    found = cc.write_combined_prompt(
        task_prompt="Implement the feature.", source=tmp_path / "absent.md", dest=dest
    )
    assert found is False
    assert dest.read_text(encoding="utf-8") == "Implement the feature."

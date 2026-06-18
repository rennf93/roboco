"""Tests for the Grok opencode.json generator (RoboCo MCP -> opencode config)."""

from __future__ import annotations

from roboco.llm.providers.opencode_config import (
    OpencodeGuards,
    build_opencode_config,
    translate_mcp_servers,
)

_MODEL = "grok-build-0.1"

_MCP = {
    "mcpServers": {
        "roboco-flow": {
            "command": "uv",
            "args": ["run", "--no-sync", "python", "-m", "roboco.mcp.flow_server"],
            "env": {
                "ROBOCO_AGENT_ID": "uuid-1",
                "UV_PROJECT_ENVIRONMENT": "/app/.venv",
            },
        },
        "roboco-do": {
            "command": "uv",
            "args": ["run", "--no-sync", "python", "-m", "roboco.mcp.do_server"],
            "env": {"ROBOCO_AGENT_ID": "uuid-1"},
        },
    }
}


def test_translate_mcp_servers_shape() -> None:
    out = translate_mcp_servers(_MCP)
    flow = out["roboco-flow"]
    assert flow["type"] == "local"
    assert flow["enabled"] is True
    # command + args collapse into a single command array (opencode shape).
    assert flow["command"] == [
        "uv",
        "run",
        "--no-sync",
        "python",
        "-m",
        "roboco.mcp.flow_server",
    ]
    # env -> environment (opencode key).
    assert flow["environment"]["ROBOCO_AGENT_ID"] == "uuid-1"
    assert "env" not in flow
    assert set(out) == {"roboco-flow", "roboco-do"}


def test_translate_mcp_servers_empty() -> None:
    assert translate_mcp_servers({}) == {}
    assert translate_mcp_servers({"mcpServers": {}}) == {}


def test_translate_mcp_servers_omits_environment_when_no_env() -> None:
    out = translate_mcp_servers(
        {"mcpServers": {"x": {"command": "uv", "args": ["run"]}}}
    )
    assert "environment" not in out["x"]
    assert out["x"]["command"] == ["uv", "run"]


def test_build_opencode_config_emits_no_provider_block() -> None:
    cfg = build_opencode_config(
        _MCP,
        _MODEL,
        instruction_paths=["/app/system-prompt.md"],
    )
    # CRITICAL: NO provider block. ANY provider.xai block breaks plugin-tool
    # registration on opencode 1.17.8 (verified live). The built-in xai provider
    # drives the model; the key reaches it via the XAI_API_KEY env var.
    assert "provider" not in cfg
    # Top-level model selector is "<provider>/<model>".
    assert cfg["model"] == "xai/grok-build-0.1"
    # Gateway servers carried through.
    assert "roboco-flow" in cfg["mcp"]
    assert cfg["instructions"] == ["/app/system-prompt.md"]


def test_build_opencode_config_has_no_plugin_array() -> None:
    # opencode 1.17.8 ignores config `plugin:`-array absolute paths for
    # registration; plugins live in the auto-discovery dir, baked into the images.
    cfg = build_opencode_config(_MCP, _MODEL, instruction_paths=[])
    assert "plugin" not in cfg


def test_build_opencode_config_edit_permission_is_tunable() -> None:
    # Read-only roles (qa / pr_reviewer / auditor / PMs / board) get edit=deny so
    # a Grok agent can't write code on a role that must never touch the tree.
    cfg = build_opencode_config(
        {},
        _MODEL,
        instruction_paths=[],
        guards=OpencodeGuards(edit_permission="deny"),
    )
    assert cfg["permission"]["edit"] == "deny"


def test_build_opencode_config_bash_permission_is_tunable() -> None:
    cfg = build_opencode_config(
        {},
        _MODEL,
        instruction_paths=[],
        guards=OpencodeGuards(bash_permission="deny"),
    )
    assert cfg["permission"]["bash"] == "deny"
    assert cfg["permission"]["edit"] == "allow"


def test_build_opencode_config_allows_external_directory_by_default() -> None:
    # opencode auto-denies an "ask" external-dir read in headless mode (the
    # pr-reviewer couldn't read a diff it wrote to /tmp); default "allow".
    cfg = build_opencode_config(_MCP, _MODEL, instruction_paths=[])
    assert cfg["permission"]["external_directory"] == "allow"


def test_build_opencode_config_external_directory_is_tunable() -> None:
    cfg = build_opencode_config(
        {},
        _MODEL,
        instruction_paths=[],
        guards=OpencodeGuards(external_directory_permission="deny"),
    )
    assert cfg["permission"]["external_directory"] == "deny"


def test_build_opencode_config_disables_subagent_task_tool_by_default() -> None:
    # The subagent `task` tool must be hard-disabled: a RoboCo role never uses
    # opencode-internal subagents, and one spawned on grok-build-0.1 hung the run.
    cfg = build_opencode_config(_MCP, _MODEL, instruction_paths=[])
    assert cfg["tools"] == {"task": False}


def test_build_opencode_config_subagents_can_be_re_enabled() -> None:
    cfg = build_opencode_config(
        _MCP,
        _MODEL,
        instruction_paths=[],
        guards=OpencodeGuards(disable_subagents=False),
    )
    assert "tools" not in cfg

"""Tests for the Grok opencode.json generator (RoboCo MCP -> opencode config)."""

from __future__ import annotations

from roboco.llm.providers.opencode_config import (
    XaiTarget,
    build_opencode_config,
    translate_mcp_servers,
)

_TARGET = XaiTarget(
    base_url="https://api.x.ai/v1", api_key="xai-key", model="grok-build-0.1"
)

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


def test_build_opencode_config_provider_and_model() -> None:
    cfg = build_opencode_config(
        _MCP,
        _TARGET,
        instruction_paths=["/app/system-prompt.md"],
    )
    provider = cfg["provider"]["xai"]
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["options"]["baseURL"] == "https://api.x.ai/v1"
    assert provider["options"]["apiKey"] == "xai-key"
    assert "grok-build-0.1" in provider["models"]
    # Top-level model selector is "<provider>/<model>".
    assert cfg["model"] == "xai/grok-build-0.1"
    # Gateway servers carried through.
    assert "roboco-flow" in cfg["mcp"]
    assert cfg["instructions"] == ["/app/system-prompt.md"]


def test_build_opencode_config_bash_permission_is_tunable() -> None:
    cfg = build_opencode_config(
        {},
        _TARGET,
        instruction_paths=[],
        bash_permission="deny",
    )
    assert cfg["permission"]["bash"] == "deny"
    assert cfg["permission"]["edit"] == "allow"

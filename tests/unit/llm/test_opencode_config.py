"""Tests for the Grok opencode.json generator (RoboCo MCP -> opencode config)."""

from __future__ import annotations

import os
from unittest.mock import patch

from roboco.llm.providers.opencode_config import (
    _DEFAULT_CHUNK_TIMEOUT_MS,
    _DEFAULT_REQUEST_TIMEOUT_MS,
    OpencodeGuards,
    XaiTarget,
    _env_int,
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
    # grok-build-0.1 needs the Responses API → @ai-sdk/openai, not -compatible.
    assert provider["npm"] == "@ai-sdk/openai"
    assert provider["options"]["baseURL"] == "https://api.x.ai/v1"
    assert provider["options"]["apiKey"] == "xai-key"
    assert "grok-build-0.1" in provider["models"]
    # Top-level model selector is "<provider>/<model>".
    assert cfg["model"] == "xai/grok-build-0.1"
    # Gateway servers carried through.
    assert "roboco-flow" in cfg["mcp"]
    assert cfg["instructions"] == ["/app/system-prompt.md"]
    # The secret-scrub command guard is wired in by default.
    assert cfg["plugin"] == ["/app/opencode-plugins/secret-scrub.js"]


def test_build_opencode_config_bash_permission_is_tunable() -> None:
    cfg = build_opencode_config(
        {},
        _TARGET,
        instruction_paths=[],
        guards=OpencodeGuards(bash_permission="deny"),
    )
    assert cfg["permission"]["bash"] == "deny"
    assert cfg["permission"]["edit"] == "allow"


def test_build_opencode_config_allows_external_directory_by_default() -> None:
    # opencode auto-denies an "ask" external-dir read in headless mode (the
    # pr-reviewer couldn't read a diff it wrote to /tmp); default "allow".
    cfg = build_opencode_config(_MCP, _TARGET, instruction_paths=[])
    assert cfg["permission"]["external_directory"] == "allow"


def test_build_opencode_config_external_directory_is_tunable() -> None:
    cfg = build_opencode_config(
        {},
        _TARGET,
        instruction_paths=[],
        guards=OpencodeGuards(external_directory_permission="ask"),
    )
    assert cfg["permission"]["external_directory"] == "ask"


def test_build_opencode_config_disables_subagent_task_tool_by_default() -> None:
    # The subagent `task` tool must be hard-disabled: a RoboCo role never uses
    # opencode-internal subagents, and one spawned on grok-build-0.1 hung the run.
    cfg = build_opencode_config(_MCP, _TARGET, instruction_paths=[])
    assert cfg["tools"] == {"task": False}


def test_build_opencode_config_subagents_can_be_re_enabled() -> None:
    cfg = build_opencode_config(
        _MCP,
        _TARGET,
        instruction_paths=[],
        guards=OpencodeGuards(disable_subagents=False),
    )
    assert "tools" not in cfg


def test_build_opencode_config_sets_default_timeouts() -> None:
    # Both timeouts land under provider.<id>.options so opencode aborts a stalled
    # request / idle stream instead of hanging the parent run forever.
    opts = build_opencode_config(_MCP, _TARGET, instruction_paths=[])["provider"][
        "xai"
    ]["options"]
    assert opts["timeout"] == _DEFAULT_REQUEST_TIMEOUT_MS
    assert opts["chunkTimeout"] == _DEFAULT_CHUNK_TIMEOUT_MS


def test_build_opencode_config_timeouts_are_tunable() -> None:
    req_ms, chunk_ms = 111_000, 22_000
    opts = build_opencode_config(
        _MCP,
        _TARGET,
        instruction_paths=[],
        guards=OpencodeGuards(request_timeout_ms=req_ms, chunk_timeout_ms=chunk_ms),
    )["provider"]["xai"]["options"]
    assert opts["timeout"] == req_ms
    assert opts["chunkTimeout"] == chunk_ms


def test_env_int_parses_and_falls_back() -> None:
    fallback = 999
    parsed = 45_000
    with patch.dict(os.environ, {"X_MS": str(parsed)}):
        assert _env_int("X_MS", fallback) == parsed
    # Missing, blank, non-integer, and non-positive all fall back to the default
    # so a bad operator override can never disable the timeout entirely.
    with patch.dict(os.environ, {}, clear=True):
        assert _env_int("X_MS", fallback) == fallback
    for bad in ("", "  ", "abc", "0", "-5"):
        with patch.dict(os.environ, {"X_MS": bad}):
            assert _env_int("X_MS", fallback) == fallback

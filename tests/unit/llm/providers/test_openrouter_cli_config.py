"""openrouter_cli_config — opencode.json rendering (agent block + permission
deny-rules + mcp passthrough + provider block) + the auth preflight."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.llm.providers import openrouter_cli_config as oc

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


# ---------------------------------------------------------------------------
# permission_for_role — deny-only, role-scoped (opencode config-file shape)
# ---------------------------------------------------------------------------


def test_fleet_wide_tool_denies_present_for_every_role() -> None:
    for role in ("developer", "qa", "pr_reviewer", "main_pm", "unknown-role-xyz"):
        perm = oc.permission_for_role(role)
        for tool in oc._FLEET_WIDE_TOOL_DENY:
            assert perm[tool] == "deny"


def test_bash_capable_role_keeps_bash_and_denies_git_destructive_pm() -> None:
    perm = oc.permission_for_role("developer")
    # bash-capable: no blanket bash deny — instead a command-glob deny map.
    assert isinstance(perm["bash"], dict)
    bash_patterns = set(perm["bash"])
    assert "git push*" in bash_patterns
    assert "rm -rf*" in bash_patterns
    assert "uv run*" in bash_patterns
    assert "env" in bash_patterns
    # Developer writes code — no edit-tool deny.
    assert "write" not in perm
    assert "edit" not in perm


def test_non_bash_role_gets_blanket_bash_deny_and_no_command_scoped_rules() -> None:
    perm = oc.permission_for_role("pr_reviewer")
    assert perm["bash"] == "deny"
    # Read-only reviewer doesn't write code either.
    assert perm["write"] == "deny"
    assert perm["edit"] == "deny"


def test_main_pm_keeps_bash_but_denies_write_edit() -> None:
    perm = oc.permission_for_role("main_pm")
    assert isinstance(perm["bash"], dict)  # PM keeps its shell
    bash_patterns = set(perm["bash"])
    assert "git push*" in bash_patterns
    assert perm["write"] == "deny"  # PM doesn't write code
    assert perm["edit"] == "deny"


def test_unknown_role_gets_every_deny_category() -> None:
    perm = oc.permission_for_role("unknown-role-xyz")
    assert perm["bash"] == "deny"
    assert perm["write"] == "deny"
    assert perm["edit"] == "deny"
    for tool in oc._FLEET_WIDE_TOOL_DENY:
        assert perm[tool] == "deny"


# ---------------------------------------------------------------------------
# render_agent_block — prompt + mode + model + permission + tools
# ---------------------------------------------------------------------------


def test_render_agent_block_carries_prompt_mode_model_permission() -> None:
    block = oc.render_agent_block("developer", "anthropic/claude-sonnet-4")
    assert block["mode"] == "primary"
    assert block["model"] == "anthropic/claude-sonnet-4"
    assert "permission" in block
    assert "tools" in block
    # The prompt is loaded from the system prompt path; empty string if absent.
    assert isinstance(block["prompt"], str)


def test_render_agent_block_permission_vararies_by_role() -> None:
    dev = oc.render_agent_block("developer", "m")
    reviewer = oc.render_agent_block("pr_reviewer", "m")
    assert isinstance(dev["permission"]["bash"], dict)
    assert reviewer["permission"]["bash"] == "deny"


# ---------------------------------------------------------------------------
# render_provider_block — OpenRouter endpoint + env name + npm package
# ---------------------------------------------------------------------------


def test_render_provider_block_has_api_env_npm() -> None:
    block = oc.render_provider_block("https://openrouter.ai/api/v1")
    provider = block["openrouter"]
    assert provider["api"] == "https://openrouter.ai/api/v1"
    assert provider["name"] == "OpenRouter"
    assert "OPENROUTER_API_KEY" in provider["env"]
    assert provider["npm"] == oc._OPENROUTER_PROVIDER_NPM


# ---------------------------------------------------------------------------
# render_mcp_block — near-passthrough of the mounted mcp-config.json
# ---------------------------------------------------------------------------


def test_render_mcp_block_passthrough_with_env() -> None:
    rendered = oc.render_mcp_block(_SAMPLE_MCP)
    flow = rendered["roboco-flow"]
    assert flow["type"] == "local"
    assert flow["command"] == [
        "uv",
        "run",
        "--no-sync",
        "python",
        "-m",
        "roboco.mcp.flow_server",
    ]
    assert flow["env"]["ROBOCO_AGENT_TOKEN"] == "tok-123"
    do = rendered["roboco-do"]
    assert "env" not in do  # no env in source → no env in rendered


def test_render_mcp_block_empty_servers() -> None:
    assert oc.render_mcp_block({}) == {}
    assert oc.render_mcp_block({"mcpServers": {}}) == {}


# ---------------------------------------------------------------------------
# render_config — the full opencode.json dict
# ---------------------------------------------------------------------------


def test_render_config_has_schema_agent_provider_mcp() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(_SAMPLE_MCP, f)
        mcp_path = f.name
    config = oc.render_config(
        "developer",
        "anthropic/claude-sonnet-4",
        "https://openrouter.ai/api/v1",
        mcp_path,
    )
    assert config["$schema"] == "https://opencode.ai/config.json"
    assert "roboco" in config["agent"]
    assert "openrouter" in config["provider"]
    assert "roboco-flow" in config["mcp"]


def test_render_config_omits_mcp_when_no_servers() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({}, f)
        mcp_path = f.name
    config = oc.render_config("developer", "m", "url", mcp_path)
    assert "mcp" not in config


# ---------------------------------------------------------------------------
# Auth preflight — static key, no expiry read (the Ollama shape)
# ---------------------------------------------------------------------------


def test_is_valid_true_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc123")
    assert oc.is_valid() is True


def test_is_valid_false_when_key_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    assert oc.is_valid() is False


def test_is_valid_false_when_key_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert oc.is_valid() is False


def test_is_valid_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "   ")
    assert oc.is_valid() is False


# ---------------------------------------------------------------------------
# main() — render mode + --check mode
# ---------------------------------------------------------------------------


def test_main_writes_opencode_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_path = tmp_path / "mcp-config.json"
    mcp_path.write_text(json.dumps(_SAMPLE_MCP), encoding="utf-8")
    config_path = tmp_path / "opencode.json"
    system_prompt = tmp_path / "system-prompt.md"
    system_prompt.write_text("blueprint", encoding="utf-8")

    monkeypatch.setattr(oc, "OPENCODE_CONFIG_PATH", config_path)
    monkeypatch.setattr(oc, "SYSTEM_PROMPT_PATH", system_prompt)
    monkeypatch.setenv("ROBOCO_AGENT_ID", "be-dev-1")
    monkeypatch.setenv("ROBOCO_MCP_CONFIG", str(mcp_path))
    monkeypatch.setenv("ROBOCO_AGENT_MODEL", "anthropic/claude-sonnet-4")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    assert oc.main([]) == 0

    rendered = json.loads(config_path.read_text(encoding="utf-8"))
    assert rendered["$schema"] == "https://opencode.ai/config.json"
    assert rendered["agent"]["roboco"]["model"] == "anthropic/claude-sonnet-4"
    assert rendered["agent"]["roboco"]["prompt"] == "blueprint"
    assert "openrouter" in rendered["provider"]
    assert "roboco-flow" in rendered["mcp"]


def test_main_check_flag_passes_when_key_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "opencode.json"
    monkeypatch.setattr(oc, "OPENCODE_CONFIG_PATH", config_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc")
    assert oc.main(["--check"]) == 0
    assert not config_path.exists()  # --check never renders


def test_main_check_flag_fails_when_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oc, "OPENCODE_CONFIG_PATH", tmp_path / "opencode.json")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert oc.main(["--check"]) == 1

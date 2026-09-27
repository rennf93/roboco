"""Unit tests for roboco.llm.providers.hummin_cli_config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from roboco.llm.providers import hummin_cli_config


def _role_of(agent_id: str) -> str:
    return {
        "be-dev-1": "developer",
        "be-qa-1": "qa",
        "be-doc-1": "documenter",
        "board-1": "board",
    }.get(agent_id, "")


def test_tools_for_role_bash_roles_get_bash_and_authors_get_writes() -> None:
    assert hummin_cli_config.tools_for_role("developer") == "read,edit,write,bash"
    assert hummin_cli_config.tools_for_role("documenter") == "read,edit,write,bash"


def test_tools_for_role_reviewer_roles_read_only() -> None:
    # pr_reviewer/board are neither bash-capable nor authors: `--tools` is a
    # STRICT allowlist, so omitting bash/write/edit IS kimi's deny mapping.
    assert hummin_cli_config.tools_for_role("pr_reviewer") == "read"


def test_tools_for_role_qa_reads_only() -> None:
    # qa is not in _BASH_ROLES and does not allow_write — the same shape
    # kimi's permission rules produce for the role.
    assert hummin_cli_config.tools_for_role("qa") == "read"


def test_render_settings_defaults_merge_preserving() -> None:
    existing = {"defaultModel": "glm-5.3", "enableInstallTelemetry": True}
    merged = hummin_cli_config.render_settings_defaults(existing)
    assert merged["defaultModel"] == "glm-5.3"
    # An operator-set key always wins (merge-preserving, never clobbered).
    assert merged["enableInstallTelemetry"] is True
    assert merged["enableAnalytics"] is False
    assert merged["defaultProjectTrust"] == "never"


def test_render_settings_defaults_from_none() -> None:
    merged = hummin_cli_config.render_settings_defaults(None)
    assert merged == {
        "enableInstallTelemetry": False,
        "enableAnalytics": False,
        "defaultProjectTrust": "never",
    }


def test_write_env_file_carries_tools_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hummin_cli_config, "get_agent_role", _role_of)
    monkeypatch.setenv("ROBOCO_AGENT_ID", "be-dev-1")
    env_file = tmp_path / "hummin-env"
    hummin_cli_config.write_env_file(env_file)
    content = env_file.read_text(encoding="utf-8")
    assert content == 'ROBOCO_HUMMIN_TOOLS="read,edit,write,bash"\n'


def test_main_renders_settings_and_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_path = tmp_path / "agent" / "settings.json"
    env_file = tmp_path / "hummin-env"
    monkeypatch.setattr(hummin_cli_config, "HUMMIN_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(hummin_cli_config, "HUMMIN_ENV_FILE", env_file)
    monkeypatch.setattr(hummin_cli_config, "get_agent_role", _role_of)
    monkeypatch.setenv("ROBOCO_AGENT_ID", "be-qa-1")

    rc = hummin_cli_config.main([])

    assert rc == 0
    rendered = json.loads(settings_path.read_text(encoding="utf-8"))
    assert rendered["defaultProjectTrust"] == "never"
    assert env_file.read_text(encoding="utf-8") == 'ROBOCO_HUMMIN_TOOLS="read"\n'


def test_main_existing_settings_never_clobbered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path = tmp_path / "agent" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"defaultProjectTrust": "always"}), encoding="utf-8"
    )
    monkeypatch.setattr(hummin_cli_config, "HUMMIN_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(hummin_cli_config, "HUMMIN_ENV_FILE", tmp_path / "env")
    monkeypatch.setenv("ROBOCO_AGENT_ID", "be-dev-1")

    hummin_cli_config.main([])

    rendered = json.loads(settings_path.read_text(encoding="utf-8"))
    assert rendered["defaultProjectTrust"] == "always"


def test_check_mode_passes_hummin_exit_code_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def _fake_run(argv: list[str], **_kw: object) -> object:
        calls.append(argv)
        return type("R", (), {"returncode": 2})()

    monkeypatch.setattr(hummin_cli_config.subprocess, "run", _fake_run)
    # 2 = hummin's own "invalid" status (verified main.ts mapping).
    assert hummin_cli_config.main(["--check"]) == 2  # noqa: PLR2004
    assert calls == [["hummin", "auth", "check", "--provider", "zai", "--json"]]


def test_check_mode_maps_missing_binary_to_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_argv: list[str], **_kw: object) -> object:
        raise OSError("no such binary")

    monkeypatch.setattr(hummin_cli_config.subprocess, "run", _boom)
    assert hummin_cli_config.main(["--check"]) == 2  # noqa: PLR2004


# ---------------------------------------------------------------------------
# MCP bridge: hummin has a native MCP client (extensions/hummin-mcp.ts) that
# reads settings `mcpServers` and registers tools as mcp_<server>_<tool>;
# --tools gates every tool by exact name, so the renderer emits the role's
# qualified verb surface + the read-only aux servers.
# ---------------------------------------------------------------------------


def test_qualified_names_match_hummin_convention() -> None:
    # sanitizeToolPart: lowercase, non-alnum runs -> "_", outer "_" stripped.
    assert (
        hummin_cli_config.qualified_mcp_tool_name("roboco-flow", "give_me_work")
        == "mcp_roboco_flow_give_me_work"
    )
    assert (
        hummin_cli_config.qualified_mcp_tool_name("roboco-git-readonly", "git_status")
        == "mcp_roboco_git_readonly_git_status"
    )


def test_load_mcp_servers_reads_mount(tmp_path: Path) -> None:
    config = tmp_path / "mcp-config.json"
    config.write_text(
        json.dumps(
            {"mcpServers": {"roboco-flow": {"command": "uv", "args": ["run", "x"]}}}
        ),
        encoding="utf-8",
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(hummin_cli_config, "MCP_CONFIG_PATH", config)
    try:
        servers = hummin_cli_config.load_mcp_servers()
    finally:
        monkeypatch.undo()
    assert list(servers) == ["roboco-flow"]


def test_load_mcp_servers_missing_mount_is_inert(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(hummin_cli_config, "MCP_CONFIG_PATH", tmp_path / "absent.json")
    try:
        assert hummin_cli_config.load_mcp_servers() == {}
    finally:
        monkeypatch.undo()


def test_render_mcp_servers_merges_and_preserves_operator_keys() -> None:
    settings: dict[str, object] = {"defaultProjectTrust": "always"}
    servers = {"roboco-flow": {"command": "uv"}}
    merged = hummin_cli_config.render_mcp_servers(settings, servers)
    assert merged["mcpServers"] == servers
    assert merged["defaultProjectTrust"] == "always"
    # No servers (no mount) -> settings untouched.
    assert hummin_cli_config.render_mcp_servers(settings, {}) == settings


def test_mcp_tool_allowlist_role_scoped_from_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allowlist exposes exactly the manifest's flow/do verbs (role-
    scoped governance) plus the mounted read-only aux servers — never a
    blanket mcp wildcard."""
    manifest = tmp_path / "tool-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "flow_tools": ["triage", "i_am_idle"],
                "do_tools": ["note", "propose_quality_report"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(hummin_cli_config, "_TOOL_MANIFEST_PATH", manifest)
    # Aux introspection stubbed: the server modules' names come from the mcp
    # registry at runtime; here we pin the assembly, not the SDK.
    monkeypatch.setattr(
        hummin_cli_config,
        "_aux_server_tool_names",
        lambda _module: ["git_status", "git_log"],
    )
    servers = {
        "roboco-flow": {"command": "uv"},
        "roboco-do": {"command": "uv"},
        "roboco-git-readonly": {"command": "uv"},
    }
    names = hummin_cli_config.mcp_tool_allowlist(servers)
    assert "mcp_roboco_flow_triage" in names
    assert "mcp_roboco_flow_i_am_idle" in names
    assert "mcp_roboco_do_note" in names
    assert "mcp_roboco_do_propose_quality_report" in names
    assert "mcp_roboco_git_readonly_git_status" in names
    # A server in the manifest map but NOT mounted contributes nothing.
    assert not any(n.startswith("mcp_roboco_optimal_") for n in names)


def test_mcp_tool_allowlist_without_servers_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hummin_cli_config, "MCP_CONFIG_PATH", Path("/nonexistent/mcp-config.json")
    )
    assert hummin_cli_config.mcp_tool_allowlist() == []

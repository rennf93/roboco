"""Unit tests for roboco.llm.providers.hummin_cli_config."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.llm.providers import hummin_cli_config
from roboco.services.gateway.role_config import ROLE_CONFIGS

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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


def test_tools_for_role_board_roles_get_the_gateway_channel() -> None:
    """hummin has no MCP client, so bash is the ONLY gateway transport (the
    workspace verb helper run under the shell). Until 2026-09-27 the board
    roles got a bare `read` allowlist: no verbs, no shell, no way to do
    their job — they wrote their report into the void and respawn-looped
    (auditor, product-owner, head-marketing live that day). Review roles
    still don't get write/edit: they are not authors."""
    assert hummin_cli_config.tools_for_role("pr_reviewer") == "read,bash"
    assert hummin_cli_config.tools_for_role("auditor") == "read,bash"
    assert hummin_cli_config.tools_for_role("product_owner") == "read,bash"
    assert hummin_cli_config.tools_for_role("head_marketing") == "read,bash"
    assert hummin_cli_config.tools_for_role("qa") == "read,bash"


def test_tools_for_role_every_configured_role_gets_bash() -> None:
    """Drift-proofing: any role in role_config must come out of
    tools_for_role with bash. Withholding bash on hummin withholds the
    gateway itself, not just a shell."""
    for role in ROLE_CONFIGS:
        tools = hummin_cli_config.tools_for_role(role)
        assert "bash" in tools.split(","), f"role {role} lost the gateway channel"


def test_tools_for_role_non_authors_do_not_get_writes() -> None:
    # The write boundary is intact: non-author roles never see edit/write.
    assert "edit" not in hummin_cli_config.tools_for_role("qa").split(",")
    assert "write" not in hummin_cli_config.tools_for_role("pr_reviewer").split(",")


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
    # The default-role env file carries the gateway channel too.
    assert env_file.read_text(encoding="utf-8") == 'ROBOCO_HUMMIN_TOOLS="read,bash"\n'


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

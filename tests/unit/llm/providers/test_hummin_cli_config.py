"""Unit tests for roboco.llm.providers.hummin_cli_config."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.llm.providers import hummin_cli_config

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

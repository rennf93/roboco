"""kimi_cli_config — config.toml (managed blocks + per-role permission rules
+ hooks) + mcp.json passthrough + AGENTS.md + the auth preflight."""

from __future__ import annotations

import json
import tomllib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from roboco.llm.providers import kimi_cli_config as kc

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
# permission_rules_for_role — deny-only, role-scoped
# ---------------------------------------------------------------------------


def test_fleet_wide_denies_present_for_every_role() -> None:
    for role in ("developer", "qa", "pr_reviewer", "main_pm", "unknown-role-xyz"):
        rules = kc.permission_rules_for_role(role)
        patterns = {r["pattern"] for r in rules}
        for fleet_wide in kc._FLEET_WIDE_DENY:
            assert fleet_wide in patterns
        assert all(r["decision"] == "deny" for r in rules)


def test_bash_capable_role_keeps_bash_and_denies_git_destructive_pm() -> None:
    rules = kc.permission_rules_for_role("developer")
    patterns = {r["pattern"] for r in rules}
    assert "Bash" not in patterns  # bash-capable: no blanket deny
    assert "Bash(git push*)" in patterns
    assert "Bash(rm -rf*)" in patterns
    assert "Bash(uv run*)" in patterns
    # Developer writes code — no edit-tool deny.
    assert "Write" not in patterns
    assert "Edit" not in patterns


def test_non_bash_role_gets_blanket_bash_deny_and_no_command_scoped_rules() -> None:
    rules = kc.permission_rules_for_role("pr_reviewer")
    patterns = {r["pattern"] for r in rules}
    assert "Bash" in patterns
    assert "Bash(git push*)" not in patterns  # blanket deny — nothing left to scope
    # Read-only reviewer doesn't write code either.
    assert "Write" in patterns
    assert "Edit" in patterns


def test_main_pm_keeps_bash_but_denies_write_edit() -> None:
    rules = kc.permission_rules_for_role("main_pm")
    patterns = {r["pattern"] for r in rules}
    assert "Bash" not in patterns  # PM keeps its shell
    assert "Bash(git push*)" in patterns
    assert "Write" in patterns  # PM doesn't write code
    assert "Edit" in patterns


def test_unknown_role_gets_every_deny_category() -> None:
    rules = kc.permission_rules_for_role("unknown-role-xyz")
    patterns = {r["pattern"] for r in rules}
    assert "Write" in patterns
    assert "Edit" in patterns
    assert "Bash" in patterns


# ---------------------------------------------------------------------------
# kimi_hooks_config
# ---------------------------------------------------------------------------


def test_kimi_hooks_config_wires_bash_guard_wrapper_no_env_field() -> None:
    # A [[hooks]] entry with an `env` key gets the WHOLE hooks section
    # silently dropped by the CLI (live-verified) — env delivery must ride
    # the wrapper script's own export, never a rendered `env` field.
    hooks = kc.kimi_hooks_config("/app/scripts/kimi-bash-guard-wrapper.sh")
    assert len(hooks) == 1
    hook = hooks[0]
    assert hook["event"] == "PreToolUse"
    assert hook["matcher"] == "Bash"
    assert hook["command"] == "/app/scripts/kimi-bash-guard-wrapper.sh"
    assert "env" not in hook


def test_kimi_hooks_config_default_points_at_wrapper() -> None:
    hooks = kc.kimi_hooks_config()
    assert hooks[0]["command"] == kc.KIMI_BASH_GUARD_WRAPPER
    assert hooks[0]["command"].endswith("kimi-bash-guard-wrapper.sh")


def test_kimi_hooks_config_entries_only_carry_legal_keys() -> None:
    # Pins the whole defect class: any future field addition to a rendered
    # hook entry that isn't one of these four gets silently dropped by kimi.
    legal_keys = {"event", "matcher", "command", "timeout"}
    for hook in kc.kimi_hooks_config():
        assert set(hook.keys()) <= legal_keys


# ---------------------------------------------------------------------------
# render_config_toml — valid TOML, managed blocks + telemetry/upgrade + rules
# ---------------------------------------------------------------------------


def test_render_config_toml_is_valid_toml() -> None:
    parsed = tomllib.loads(kc.render_config_toml("developer"))
    assert parsed["telemetry"] is False
    assert parsed["upgrade"]["auto_install"] is False


def test_render_config_toml_managed_provider_block() -> None:
    parsed = tomllib.loads(kc.render_config_toml("developer"))
    provider = parsed["providers"]["managed:kimi-code"]
    assert provider["type"] == "kimi"
    assert provider["base_url"] == "https://api.kimi.com/coding/v1"
    assert provider["oauth"]["storage"] == "file"
    assert provider["oauth"]["key"] == "oauth/kimi-code"


def test_render_config_toml_carries_all_four_model_aliases() -> None:
    parsed = tomllib.loads(kc.render_config_toml("developer"))
    models = parsed["models"]
    for alias in (
        "kimi-code/k3",
        "kimi-code/k3-256k",
        "kimi-code/kimi-for-coding",
        "kimi-code/kimi-for-coding-highspeed",
    ):
        assert alias in models
        assert models[alias]["provider"] == "managed:kimi-code"
        assert models[alias]["max_context_size"] > 0
        # The `model` value is the CLI-side managed name the wire sees —
        # exactly the alias's last segment, never a raw API id like
        # "kimi-k3" (a live-capture drift that would break every run).
        assert models[alias]["model"] == alias.removeprefix("kimi-code/")
        assert "thinking" in models[alias]["capabilities"]
    # Only the K3 family exposes reasoning effort knobs.
    assert models["kimi-code/k3"]["default_effort"] == "high"
    assert "default_effort" not in models["kimi-code/kimi-for-coding"]


def test_render_config_toml_services_share_the_managed_oauth() -> None:
    parsed = tomllib.loads(kc.render_config_toml("developer"))
    for service in ("moonshot_search", "moonshot_fetch"):
        assert parsed["services"][service]["oauth"]["key"] == "oauth/kimi-code"


def test_render_config_toml_permission_rules_vary_by_role() -> None:
    # developer keeps its shell -> gets the full command-scoped git/destructive/
    # raw-PM deny list underneath it; pr_reviewer's blanket Bash deny needs no
    # command-scoped rules at all, so it ends up with FEWER total rules despite
    # also denying Write/Edit on top of the fleet-wide set.
    dev_rules = tomllib.loads(kc.render_config_toml("developer"))["permission"]["rules"]
    reviewer_rules = tomllib.loads(kc.render_config_toml("pr_reviewer"))["permission"][
        "rules"
    ]
    assert len(dev_rules) > len(reviewer_rules)


def test_render_config_toml_hooks_present() -> None:
    parsed = tomllib.loads(kc.render_config_toml("developer"))
    assert parsed["hooks"][0]["event"] == "PreToolUse"


# ---------------------------------------------------------------------------
# render_mcp_json — near-passthrough of the mounted mcp-config.json
# ---------------------------------------------------------------------------


def test_render_mcp_json_injects_env_and_omits_empty_env() -> None:
    rendered = json.loads(kc.render_mcp_json(_SAMPLE_MCP))
    flow = rendered["mcpServers"]["roboco-flow"]
    assert flow["command"] == "uv"
    assert flow["args"][:2] == ["run", "--no-sync"]
    assert flow["env"]["ROBOCO_AGENT_TOKEN"] == "tok-123"
    assert "env" not in rendered["mcpServers"]["roboco-do"]


def test_render_mcp_json_empty_servers() -> None:
    assert json.loads(kc.render_mcp_json({})) == {"mcpServers": {}}


# ---------------------------------------------------------------------------
# write_agents_md
# ---------------------------------------------------------------------------


def test_write_agents_md_installs_the_blueprint(tmp_path: Path) -> None:
    src = tmp_path / "system-prompt.md"
    src.write_text("You are a RoboCo backend developer.", encoding="utf-8")
    dest = tmp_path / ".kimi-code" / "AGENTS.md"
    assert kc.write_agents_md(source=src, dest=dest) is True
    assert dest.read_text(encoding="utf-8") == "You are a RoboCo backend developer."


def test_write_agents_md_noops_when_source_absent(tmp_path: Path) -> None:
    dest = tmp_path / ".kimi-code" / "AGENTS.md"
    assert kc.write_agents_md(source=tmp_path / "absent.md", dest=dest) is False
    assert not dest.exists()


# ---------------------------------------------------------------------------
# Auth preflight — a plain expires_at JSON field, no JWT decode
# ---------------------------------------------------------------------------


def _write_creds(path: Path, *, expires_at: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_at": expires_at,
                "expires_in": 900,
                "scope": "chat",
                "token_type": "Bearer",
            }
        ),
        encoding="utf-8",
    )


def test_is_valid_true_for_future_unix_timestamp(tmp_path: Path) -> None:
    creds = tmp_path / "credentials" / "kimi-code.json"
    future = datetime.now(UTC) + timedelta(minutes=10)
    _write_creds(creds, expires_at=future.timestamp())
    assert kc.is_valid(creds) is True


def test_is_valid_false_for_past_unix_timestamp(tmp_path: Path) -> None:
    creds = tmp_path / "credentials" / "kimi-code.json"
    past = datetime.now(UTC) - timedelta(minutes=10)
    _write_creds(creds, expires_at=past.timestamp())
    assert kc.is_valid(creds) is False


def test_is_valid_accepts_iso8601_string(tmp_path: Path) -> None:
    creds = tmp_path / "credentials" / "kimi-code.json"
    future = datetime.now(UTC) + timedelta(minutes=10)
    _write_creds(creds, expires_at=future.isoformat())
    assert kc.is_valid(creds) is True


def test_is_valid_false_for_missing_file(tmp_path: Path) -> None:
    assert kc.is_valid(tmp_path / "credentials" / "kimi-code.json") is False


def test_is_valid_false_for_unparseable_expires_at(tmp_path: Path) -> None:
    creds = tmp_path / "credentials" / "kimi-code.json"
    _write_creds(creds, expires_at="not-a-timestamp")
    assert kc.is_valid(creds) is False


def test_seconds_until_expiry_respects_skew(tmp_path: Path) -> None:
    creds = tmp_path / "credentials" / "kimi-code.json"
    soon = datetime.now(UTC) + timedelta(seconds=30)
    _write_creds(creds, expires_at=soon.timestamp())
    assert kc.is_valid(creds, skew_seconds=60) is False
    assert kc.is_valid(creds, skew_seconds=0) is True


# ---------------------------------------------------------------------------
# main() — render mode + --check mode
# ---------------------------------------------------------------------------


def test_main_writes_config_mcp_and_agents_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_path = tmp_path / "mcp-config.json"
    mcp_path.write_text(json.dumps(_SAMPLE_MCP), encoding="utf-8")
    config_path = tmp_path / ".kimi-code" / "config.toml"
    mcp_out_path = tmp_path / ".kimi-code" / "mcp.json"
    agents_md_path = tmp_path / ".kimi-code" / "AGENTS.md"
    system_prompt = tmp_path / "system-prompt.md"
    system_prompt.write_text("blueprint", encoding="utf-8")

    monkeypatch.setattr(kc, "KIMI_CONFIG_PATH", config_path)
    monkeypatch.setattr(kc, "KIMI_MCP_PATH", mcp_out_path)
    monkeypatch.setattr(kc, "KIMI_AGENTS_MD_PATH", agents_md_path)
    monkeypatch.setattr(kc, "SYSTEM_PROMPT_PATH", system_prompt)
    monkeypatch.setenv("ROBOCO_AGENT_ID", "be-dev-1")
    monkeypatch.setenv("ROBOCO_MCP_CONFIG", str(mcp_path))

    assert kc.main([]) == 0

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["providers"]["managed:kimi-code"]["type"] == "kimi"
    rendered_mcp = json.loads(mcp_out_path.read_text(encoding="utf-8"))
    assert rendered_mcp["mcpServers"]["roboco-flow"]["env"]["ROBOCO_AGENT_TOKEN"] == (
        "tok-123"
    )
    assert agents_md_path.read_text(encoding="utf-8") == "blueprint"


def test_main_check_flag_runs_preflight_without_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    creds = tmp_path / "credentials" / "kimi-code.json"
    future = datetime.now(UTC) + timedelta(minutes=10)
    _write_creds(creds, expires_at=future.timestamp())
    config_path = tmp_path / ".kimi-code" / "config.toml"

    monkeypatch.setattr(kc, "KIMI_CREDENTIALS_PATH", creds)
    monkeypatch.setattr(kc, "KIMI_CONFIG_PATH", config_path)

    assert kc.main(["--check"]) == 0
    assert not config_path.exists()  # --check never renders


def test_main_check_flag_fails_on_missing_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        kc, "KIMI_CREDENTIALS_PATH", tmp_path / "credentials" / "kimi-code.json"
    )
    assert kc.main(["--check"]) == 1

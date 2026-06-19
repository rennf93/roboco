"""#167: agents could never Edit/Write any file (smoke-10..14).

Root cause: _generate_agent_settings put ``Write(*)``/``Edit(*)`` in the
GLOBAL base_deny. Claude Code evaluates rules deny -> ask -> allow and the
first match wins, so a deny ALWAYS beats a more-specific allow (the glob
syntax has no negation). The global deny therefore unconditionally
shadowed every per-role workspace-scoped Write/Edit allow — every agent,
developers included, got "Edit exists but is not enabled in this context"
and fell back to destructive bash redirection (clobbering real files;
e.g. a 207-line README rewritten to a 3-line stub, which QA correctly
failed).

Second defect: the workspace allow used a SINGLE leading slash
(``Write(/data/...)``). Claude Code resolves a single ``/`` against the
settings.json project root, not the container filesystem root, so even
without the global deny the allow never matched. Absolute container
paths require the ``//`` form.

Fix: drop Write(*)/Edit(*) from base_deny (roles that must not write keep
their OWN Write(*)/Edit(*) deny); emit the workspace allow in the ``//``
absolute form.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        return AgentOrchestrator.__new__(AgentOrchestrator)


_WS = "/data/workspaces/roboco-api/backend/be-dev-1"
_CELL = "/data/workspaces/roboco-api/backend"

_WRITER_ROLES = ("developer", "documenter", "product_owner", "head_marketing")
_NON_WRITER_ROLES = ("qa", "cell_pm", "main_pm", "auditor")


def test_generated_settings_base_deny_has_no_global_write_edit() -> None:
    """The settings file a developer is spawned with must NOT globally
    deny Write/Edit (that shadowed the workspace allow → unusable)."""
    orch = _orch()
    path = orch._generate_agent_settings(
        agent_id="be-dev-1",
        role="developer",
        workspace_path=_WS,
        cell_workspace_path=_CELL,
    )
    settings = json.loads(Path(path).read_text())
    deny = settings["permissions"]["deny"]
    allow = settings["permissions"]["allow"]

    assert "Write(*)" not in deny, deny
    assert "Edit(*)" not in deny, deny
    # The security denies that DO rely on deny-always-wins must remain.
    assert "Bash(git:*)" in deny, deny
    assert any(".git/config" in d for d in deny), deny
    # Workspace allow present and in the // absolute form.
    assert f"Write(/{_WS}/**)" in allow, allow
    assert f"Edit(/{_WS}/**)" in allow, allow


def test_writer_roles_use_double_slash_absolute_allow() -> None:
    """Every role that authors files emits Write/Edit allow rules in the
    // absolute-filesystem form (single / silently never matches)."""
    orch = _orch()
    for role in _WRITER_ROLES:
        perms = orch._get_role_permissions(
            role=role, workspace_path=_WS, cell_workspace_path=_CELL
        )
        write_edit = [e for e in perms["allow"] if e.startswith(("Write(", "Edit("))]
        assert write_edit, f"{role} should allow some Write/Edit: {perms}"
        for entry in write_edit:
            inner = entry[entry.index("(") + 1 :]
            assert inner.startswith("//"), (
                f"{role} allow rule must use // absolute form: {entry}"
            )


def test_non_writer_roles_still_deny_write_edit() -> None:
    """Removing the GLOBAL deny must not let QA / PMs / auditor write —
    they carry their own Write(*)/Edit(*) deny in the role config."""
    orch = _orch()
    for role in _NON_WRITER_ROLES:
        perms = orch._get_role_permissions(
            role=role, workspace_path=_WS, cell_workspace_path=_CELL
        )
        assert "Write(*)" in perms["deny"], f"{role}: {perms}"
        assert "Edit(*)" in perms["deny"], f"{role}: {perms}"


def test_non_writer_generated_settings_block_write() -> None:
    """End-to-end: a cell_pm's generated settings still deny Write/Edit
    (their own role deny survives the base_deny change)."""
    orch = _orch()
    path = orch._generate_agent_settings(
        agent_id="be-pm",
        role="cell_pm",
        workspace_path=_WS,
        cell_workspace_path=_CELL,
    )
    deny = json.loads(Path(path).read_text())["permissions"]["deny"]
    assert "Write(*)" in deny, deny
    assert "Edit(*)" in deny, deny


def test_grok_xai_settings_has_full_hooks_only() -> None:
    """Grok (xai provider) must receive the *full* set of runtime hooks via
    top-level 'hooks' in its user-settings.json equivalent. No permissions
    block (grok uses different mechanism); all 9 hook scripts registered
    under the Claude-compatible event names for parity.
    """
    orch = _orch()
    path = orch._generate_agent_settings(
        agent_id="be-dev-1",
        role="developer",
        workspace_path=_WS,
        cell_workspace_path=_CELL,
        provider_type="xai",
    )
    data = json.loads(Path(path).read_text())
    assert "hooks" in data, "grok settings must have hooks"
    assert "permissions" not in data, (
        "grok settings must omit permissions (claude-only)"
    )
    hooks = data["hooks"]
    # Full set of lifecycle hooks
    for key in [
        "SessionStart",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "UserPromptSubmit",
        "PreCompact",
        "SessionEnd",
    ]:
        assert key in hooks, f"missing hook event for full set: {key}"
    # Verify concrete scripts are wired (all hook scripts)
    flat = str(hooks)
    for script in [
        "sdk-startup-hook.sh",
        "bash-guard-hook.sh",
        "a2a-check-hook.sh",
        "post-tool-budget-hook.sh",
        "usage-report-hook.sh",
        "stop-hook.sh",
        "user-prompt-hook.sh",
        "pre-compact-hook.sh",
        "session-end-hook.sh",
    ]:
        assert script in flat, f"full set requires {script} registered for Grok"


# New coverage for AC2/AC4: grok_cli_config (not just orchestrator) writes the
# full set of hook JSONs when invoked on container start.
def test_grok_cli_config_writes_full_hooks_and_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    """grok_cli_config.write_grok_hooks (and main) emit hook JSONs for every
    required event (AC2) + AGENTS.md + config.toml (MCP) + role args (denies).
    """
    # Import inside after Path.home patch: module level constants eval Path.home
    import roboco.llm.providers.grok_cli_config as gcc  # noqa: PLC0415

    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: fake_home, raising=False)
    # Override module globals used by writers
    monkeypatch.setattr(gcc, "GROK_HOOKS_DIR", fake_home / ".grok" / "hooks")
    monkeypatch.setattr(gcc, "GROK_AGENTS_PATH", fake_home / ".grok" / "AGENTS.md")
    monkeypatch.setattr(
        gcc, "GROK_USER_SETTINGS_PATH", fake_home / ".grok" / "user-settings.json"
    )
    monkeypatch.setattr(gcc, "GROK_CONFIG_PATH", fake_home / ".grok" / "config.toml")
    monkeypatch.setattr(gcc, "GROK_ARGS_PATH", tmp_path / "grok-args")

    # Provide minimal system prompt and mcp for writers
    (tmp_path / "system.md").write_text(
        "# test blueprint for grok\nYou are a test agent."
    )
    monkeypatch.setattr(gcc, "SYSTEM_PROMPT_PATH", tmp_path / "system.md")
    mcp = tmp_path / "mcp.json"
    mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "flow": {
                        "command": "uv",
                        "args": [
                            "run",
                            "--no-sync",
                            "python",
                            "-m",
                            "roboco.mcp.flow_server",
                        ],
                    }
                }
            }
        )
    )

    monkeypatch.setenv("ROBOCO_MCP_CONFIG", str(mcp))
    monkeypatch.setenv("ROBOCO_AGENT_ID", "be-dev-1")

    # Call writer directly (what grok entrypoint invokes on start)
    hooks_dir = gcc.GROK_HOOKS_DIR
    gcc.write_grok_hooks(hooks_dir=hooks_dir, hook_path="/nonexistent/guard.sh")
    # guard file absent is tolerated (write_grok_hooks proceeds)

    # Verify full event JSONs exist (written by grok_cli_config = AC2)
    assert hooks_dir.exists()
    event_map = {
        "sessionstart": "SessionStart",
        "pretooluse": "PreToolUse",
        "posttooluse": "PostToolUse",
        "stop": "Stop",
        "userpromptsubmit": "UserPromptSubmit",
        "precompact": "PreCompact",
        "sessionend": "SessionEnd",
    }
    for event, cap in event_map.items():
        p = hooks_dir / f"roboco-{event}.json"
        assert p.exists(), f"missing hook json for {event}"
        data = json.loads(p.read_text())
        assert "hooks" in data, "hook json must contain hooks root"
        hooksec = data.get("hooks", {})
        assert cap in hooksec or cap in str(data), f"missing event {cap}"

    # user-settings also has full hooks
    us = gcc.GROK_USER_SETTINGS_PATH
    assert us.exists()
    usdata = json.loads(us.read_text())
    assert "hooks" in usdata
    for key in ["SessionStart", "PreToolUse", "Stop", "SessionEnd"]:
        assert key in usdata["hooks"]

    # AGENTS + config + role args (with --deny) written by main()
    gcc.main()
    assert gcc.GROK_AGENTS_PATH.exists()
    assert "test blueprint" in gcc.GROK_AGENTS_PATH.read_text()
    cfg = gcc.GROK_CONFIG_PATH
    assert cfg.exists()
    assert "mcp_servers" in cfg.read_text() or "flow" in cfg.read_text()
    assert gcc.GROK_ARGS_PATH.exists()
    args = gcc.GROK_ARGS_PATH.read_text()
    assert "--always-approve" in args
    assert "--disallowed-tools" in args or "--deny" in args

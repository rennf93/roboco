"""Claude Code capability lockdown.

Two independent tightenings against the shared Claude Code harness state:

1. The host's ~/.claude (OAuth credential store) and ~/.claude.json are
   bind-mounted read-write into EVERY agent container (the shared
   subscription auth every spawned agent uses — see
   AgentOrchestrator._build_mount_args). No role's job requires the LLM to
   read its own harness's credentials, so the generated settings.json must
   deny the native Read tool from the two files that carry them.
2. `--disable-slash-commands` must accompany every container spawn: skills
   resolve independently of the `--tools` built-in allowlist (Anthropic's
   own `--bare` docs note skills still resolve via `/skill-name` even with
   everything else disabled), so if the shared `~/.claude` mount ever
   carries personal skills/plugins they must not become callable inside an
   agent's session.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from roboco.models.runtime import OrchestratorAgentConfig, SpawnGitContext
from roboco.runtime.orchestrator import AgentOrchestrator

_WS = "/data/workspaces/roboco-api/backend/be-dev-1"
_CELL = "/data/workspaces/roboco-api/backend"


def _orch() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        return AgentOrchestrator.__new__(AgentOrchestrator)


def _make_dev_config() -> OrchestratorAgentConfig:
    return OrchestratorAgentConfig(
        agent_id="be-dev-1",
        blueprint_path=Path("/app/agents/blueprints/be-dev-1.md"),
        model="sonnet",
        mcp_config_path=Path("/app/mcp-config.json"),
        git_context=SpawnGitContext(
            project_slug="roboco-api",
            branch_name="feature/backend/TASK0001",
        ),
    )


def _build_image_args() -> list[str]:
    cmd: list[str] = []
    with patch(
        "roboco.runtime.orchestrator._resolve_agent_cli_model",
        return_value="claude-sonnet-5",
    ):
        AgentOrchestrator._append_image_and_claude_args(cmd, _make_dev_config(), None)
    return cmd


class TestSharedClaudeCredentialsDenied:
    """Every role's generated settings.json blocks the Read tool from the
    shared ~/.claude OAuth credential store."""

    def test_developer_settings_deny_claude_credentials(self) -> None:
        orch = _orch()
        path = orch._generate_agent_settings(
            agent_id="be-dev-1",
            role="developer",
            workspace_path=_WS,
            cell_workspace_path=_CELL,
        )
        deny = json.loads(Path(path).read_text())["permissions"]["deny"]
        assert "Read(//home/agent/.claude/.credentials.json)" in deny, deny
        assert "Read(//home/agent/.claude.json)" in deny, deny

    def test_qa_settings_also_deny_claude_credentials(self) -> None:
        """Not just the writer roles — a read-only role gets the same base_deny."""
        orch = _orch()
        path = orch._generate_agent_settings(
            agent_id="be-qa",
            role="qa",
            workspace_path=_WS,
            cell_workspace_path=_CELL,
        )
        deny = json.loads(Path(path).read_text())["permissions"]["deny"]
        assert "Read(//home/agent/.claude/.credentials.json)" in deny, deny
        assert "Read(//home/agent/.claude.json)" in deny, deny

    def test_deny_uses_absolute_double_slash_form(self) -> None:
        """Per the #167 gotcha: a single leading / resolves against the
        settings.json project root, not the container filesystem root — an
        absolute container path deny needs the // form or it silently never
        matches."""
        orch = _orch()
        path = orch._generate_agent_settings(
            agent_id="be-dev-1",
            role="developer",
            workspace_path=_WS,
            cell_workspace_path=_CELL,
        )
        deny = json.loads(Path(path).read_text())["permissions"]["deny"]
        claude_denies = [d for d in deny if d.startswith("Read(") and ".claude" in d]
        assert claude_denies, deny
        for entry in claude_denies:
            inner = entry[entry.index("(") + 1 :]
            assert inner.startswith("//"), f"must use // absolute form: {entry}"


class TestSlashCommandsDisabled:
    """--disable-slash-commands accompanies every container agent spawn."""

    def test_cmd_contains_disable_slash_commands_flag(self) -> None:
        cmd = _build_image_args()
        assert "--disable-slash-commands" in cmd, (
            f"--disable-slash-commands missing from spawn cmd — skills resolve "
            f"independently of --tools, so a contaminated shared ~/.claude mount "
            f"could still expose host skills/plugins. Full cmd: {cmd}"
        )

    def test_tools_flag_still_present(self) -> None:
        """The new flag must not crowd out or replace the existing --tools cap."""
        cmd = _build_image_args()
        assert "--tools" in cmd
        idx = cmd.index("--tools")
        assert cmd[idx + 1] == "Read,Write,Edit,Bash,Grep,Glob,TodoWrite"


class TestFableModeHooksInjection:
    """Fable-mode hooks are additive to settings.json, gated by the flag."""

    def test_fable_hooks_absent_when_flag_disabled(self) -> None:
        with patch("roboco.config.settings.fable_mode_enabled", False):
            orch = _orch()
            path = orch._generate_agent_settings(
                agent_id="be-dev-1",
                role="developer",
                workspace_path=_WS,
                cell_workspace_path=_CELL,
            )
        hooks = json.loads(Path(path).read_text())["hooks"]
        assert "SubagentStop" not in hooks
        stop_cmds = [h["command"] for g in hooks["Stop"] for h in g["hooks"]]
        assert not any("fable" in c for c in stop_cmds)

    def test_fable_hooks_present_when_flag_enabled(self) -> None:
        with patch("roboco.config.settings.fable_mode_enabled", True):
            orch = _orch()
            path = orch._generate_agent_settings(
                agent_id="be-dev-1",
                role="developer",
                workspace_path=_WS,
                cell_workspace_path=_CELL,
            )
        hooks = json.loads(Path(path).read_text())["hooks"]
        stop_cmds = [h["command"] for g in hooks["Stop"] for h in g["hooks"]]
        assert stop_cmds[-1] == "/app/scripts/fable-stop-gate-hook.sh"  # appended last
        assert stop_cmds[0] == "/app/scripts/stop-hook.sh"  # RoboCo's check still first
        subagent_cmds = [
            h["command"] for g in hooks["SubagentStop"] for h in g["hooks"]
        ]
        assert subagent_cmds == ["/app/scripts/fable-stop-gate-hook.sh subagent"]
        pretool_bash = [
            h["command"]
            for g in hooks["PreToolUse"]
            if g.get("matcher") == "Bash"
            for h in g["hooks"]
        ]
        assert "/app/scripts/bash-guard-hook.sh" in pretool_bash  # existing guard kept
        assert "/app/scripts/fable-bash-discipline-hook.sh" in pretool_bash
        posttool_bash = [
            h["command"]
            for g in hooks["PostToolUse"]
            if g.get("matcher") == "Bash"
            for h in g["hooks"]
        ]
        assert posttool_bash == ["/app/scripts/fable-honesty-nudge-hook.sh"]  # new

    def test_fable_hooks_off_leaves_hooks_dict_unchanged(self) -> None:
        """Regression guard: flag-off output equals a captured pre-Phase-2 baseline."""
        with patch("roboco.config.settings.fable_mode_enabled", False):
            orch = _orch()
            path = orch._generate_agent_settings(
                agent_id="be-dev-1",
                role="developer",
                workspace_path=_WS,
                cell_workspace_path=_CELL,
            )
        hooks = json.loads(Path(path).read_text())["hooks"]
        assert set(hooks.keys()) == {
            "SessionStart",
            "PreToolUse",
            "PostToolUse",
            "Stop",
            "UserPromptSubmit",
            "PreCompact",
            "SessionEnd",
        }

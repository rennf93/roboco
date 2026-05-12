"""Wave E3: agent spawn cmd uses --strict-mcp-config to suppress builtin
Anthropic connectors (Gmail / Google Calendar / Notion / Google Drive).

Without --strict-mcp-config, the Claude Code CLI auto-registers its
builtin MCP connectors alongside our --mcp-config entries — they appear
in the agent's tool inventory as `mcp__claude_ai_Gmail__authenticate`
etc. The flag tells the CLI to load ONLY the servers from --mcp-config.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from roboco.models.runtime import OrchestratorAgentConfig, SpawnGitContext
from roboco.runtime.orchestrator import AgentOrchestrator

_MAX_FLAG_ADJACENCY = 3


def _make_dev_config() -> OrchestratorAgentConfig:
    """Minimal AgentConfig for a developer."""
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


def _build_image_args(config: OrchestratorAgentConfig) -> list[str]:
    """Invoke _append_image_and_claude_args against an empty cmd."""
    cmd: list[str] = []
    with patch(
        "roboco.runtime.orchestrator._resolve_agent_cli_model",
        return_value="claude-sonnet-4-6",
    ):
        AgentOrchestrator._append_image_and_claude_args(cmd, config, None)
    return cmd


class TestSpawnStrictMcpConfig:
    """Agent spawn cmd includes --strict-mcp-config."""

    def test_cmd_contains_strict_mcp_config_flag(self) -> None:
        """docker run cmd for an agent includes --strict-mcp-config."""
        cmd = _build_image_args(_make_dev_config())
        assert "--strict-mcp-config" in cmd, (
            f"--strict-mcp-config flag missing from spawn cmd. Without it, "
            f"the Claude CLI auto-registers builtin connectors (Gmail, "
            f"Calendar, Notion, Drive) alongside our roboco MCP servers. "
            f"Full cmd: {cmd}"
        )

    def test_strict_mcp_config_paired_with_mcp_config(self) -> None:
        """--strict-mcp-config appears alongside --mcp-config /app/mcp-config.json."""
        cmd = _build_image_args(_make_dev_config())
        assert "--mcp-config" in cmd
        mcp_idx = cmd.index("--mcp-config")
        assert cmd[mcp_idx + 1] == "/app/mcp-config.json"
        strict_idx = cmd.index("--strict-mcp-config")
        assert abs(strict_idx - mcp_idx) <= _MAX_FLAG_ADJACENCY, (
            f"--strict-mcp-config should appear near --mcp-config; "
            f"strict_idx={strict_idx}, mcp_idx={mcp_idx}. Cmd: {cmd}"
        )

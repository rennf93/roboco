"""Per-role effort override: CLAUDE_CODE_EFFORT_LEVEL is injected into the
agent container env only for roles present in ROLE_EFFORT_MAP (default-inert)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from roboco.models.runtime import OrchestratorAgentConfig, SpawnGitContext
from roboco.runtime.orchestrator import AgentOrchestrator

_HOSTS: dict[str, str | None] = {
    "prompt": "/host/prompt.md",
    "docs": "/host/docs",
    "workspaces": "/host/workspaces",
    "mcp_config": "/host/mcp.json",
}


def _config() -> OrchestratorAgentConfig:
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


def test_no_effort_env_when_map_empty() -> None:
    with patch("roboco.runtime.orchestrator.ROLE_EFFORT_MAP", {}):
        env = AgentOrchestrator._core_volume_and_env_args(
            _config(), _HOSTS, "developer"
        )
    assert not any("CLAUDE_CODE_EFFORT_LEVEL" in item for item in env)


def test_effort_env_injected_for_mapped_role() -> None:
    with patch("roboco.runtime.orchestrator.ROLE_EFFORT_MAP", {"developer": "low"}):
        env = AgentOrchestrator._core_volume_and_env_args(
            _config(), _HOSTS, "developer"
        )
    assert "CLAUDE_CODE_EFFORT_LEVEL=low" in env


def test_effort_env_absent_for_unmapped_role() -> None:
    with patch("roboco.runtime.orchestrator.ROLE_EFFORT_MAP", {"developer": "low"}):
        env = AgentOrchestrator._core_volume_and_env_args(_config(), _HOSTS, "qa")
    assert not any("CLAUDE_CODE_EFFORT_LEVEL" in item for item in env)

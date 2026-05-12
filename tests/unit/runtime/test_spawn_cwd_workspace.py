"""Wave A2+A3 (2026-05-12): agent container cwd is set to the workspace path
so Edit(README.md) and git add README.md resolve inside the workspace clone.

Smoke run 3 showed Edit failing with 'Edit exists but is not enabled in this
context' and commit failing with 'outside repository at <workspace>' — both
caused by the container WORKDIR being /app (the roboco package) instead of
the agent's task workspace.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from roboco.models.runtime import OrchestratorAgentConfig, SpawnGitContext
from roboco.runtime.orchestrator import AgentOrchestrator


def _make_dev_config(*, project_slug: str = "roboco-api") -> OrchestratorAgentConfig:
    """Minimal AgentConfig for be-dev-1 with a known project slug."""
    return OrchestratorAgentConfig(
        agent_id="be-dev-1",
        blueprint_path=Path("/app/agents/blueprints/be-dev-1.md"),
        model="sonnet",
        mcp_config_path=Path("/app/mcp-config.json"),
        git_context=SpawnGitContext(
            project_slug=project_slug,
            branch_name="feature/backend/TASK0001",
        ),
    )


def _make_cell_pm_config(
    *, project_slug: str = "roboco-api"
) -> OrchestratorAgentConfig:
    """Minimal AgentConfig for be-pm (cell_pm role) — no per-agent workspace."""
    return OrchestratorAgentConfig(
        agent_id="be-pm",
        blueprint_path=Path("/app/agents/blueprints/be-pm.md"),
        model="sonnet",
        mcp_config_path=Path("/app/mcp-config.json"),
        git_context=SpawnGitContext(project_slug=project_slug),
    )


def _make_documenter_config(
    *, project_slug: str = "roboco-api"
) -> OrchestratorAgentConfig:
    """Minimal AgentConfig for be-doc (documenter role)."""
    return OrchestratorAgentConfig(
        agent_id="be-doc",
        blueprint_path=Path("/app/agents/blueprints/be-doc.md"),
        model="haiku",
        mcp_config_path=Path("/app/mcp-config.json"),
        git_context=SpawnGitContext(project_slug=project_slug),
    )


def _minimal_hosts() -> dict[str, str | None]:
    """Minimal host-paths dict that satisfies _build_mount_args without real FS."""
    return {
        "claude": "/home/runner/.claude",
        "blueprints": "/app/agents/blueprints",
        "docs": "/app/docs",
        "workspaces": "/data/workspaces",
        "mcp_config": "/app/mcp-config.json",
        "prompt": "/app/system-prompt.md",
        "settings": None,
        "briefing": None,
    }


def _mock_settings() -> dict[str, object]:
    """Attribute dict for patched settings object."""
    return {
        "agent_tool_call_warn": 80,
        "agent_tool_call_halt": 100,
        "agent_loop_threshold": 5,
        "agent_loop_window": 10,
        "agent_stop_attempt_allowance": 2,
        "manifest_host_dir": "/tmp/manifests",
        "workspaces_root": "/data/workspaces",
    }


def _build_cmd(container_name: str, config: OrchestratorAgentConfig) -> list[str]:
    """Call _build_mount_args with all external I/O patched out."""
    hosts = _minimal_hosts()
    attrs = _mock_settings()
    with (
        patch("roboco.runtime.orchestrator.settings") as mock_settings,
        patch("roboco.runtime.orchestrator.Path.exists", return_value=False),
        patch(
            "roboco.runtime.orchestrator._build_manifest_for_agent",
            return_value=None,
        ),
    ):
        for k, v in attrs.items():
            setattr(mock_settings, k, v)
        return AgentOrchestrator._build_mount_args(container_name, config, hosts)


class TestDeveloperSpawnCwdWorkspace:
    """Developer container must start in the agent's task workspace."""

    def test_cmd_contains_workdir_flag(self) -> None:
        """docker run for a developer includes -w <workspace_path>."""
        config = _make_dev_config(project_slug="roboco-api")
        cmd = _build_cmd("roboco-agent-be-dev-1", config)

        assert "-w" in cmd, f"'-w' flag missing from docker run cmd: {cmd}"
        w_idx = cmd.index("-w")
        workdir = cmd[w_idx + 1]
        # Developer workspace: /data/workspaces/<project>/<team>/<agent>
        expected = "/data/workspaces/roboco-api/backend/be-dev-1"
        assert workdir == expected, (
            f"Expected workdir '{expected}' but got '{workdir}'. "
            f"Full cmd: {cmd}"
        )

    def test_workdir_matches_edit_allowlist_path(self) -> None:
        """The -w value matches the Edit({workspace_path}/**) allowlist prefix."""
        config = _make_dev_config(project_slug="my-project")
        cmd = _build_cmd("roboco-agent-be-dev-1", config)

        w_idx = cmd.index("-w")
        workdir = cmd[w_idx + 1]
        # Allowlist in _get_role_permissions: Edit({workspace_path}/**)
        # workdir must equal that workspace_path
        assert workdir == "/data/workspaces/my-project/backend/be-dev-1"


class TestCellPmSpawnCwdNoWorkdir:
    """cell_pm containers must NOT get a -w flag (no per-agent write workspace)."""

    def test_cmd_does_not_contain_workdir_flag(self) -> None:
        """docker run for a cell_pm omits -w so container falls back to /app."""
        config = _make_cell_pm_config(project_slug="roboco-api")
        cmd = _build_cmd("roboco-agent-be-pm", config)

        assert "-w" not in cmd, (
            "cell_pm should not have '-w' in docker run cmd "
            f"(would shadow /app Dockerfile WORKDIR): {cmd}"
        )


class TestDocumenterSpawnCwdCellWorkspace:
    """Documenter container must start in the cell-workspace path."""

    def test_cmd_contains_cell_workspace_workdir(self) -> None:
        """docker run for a documenter uses -w <cell_workspace_path>."""
        config = _make_documenter_config(project_slug="roboco-api")
        cmd = _build_cmd("roboco-agent-be-doc", config)

        # Documenter allowlist scopes to cell_workspace_path:
        # /data/workspaces/<project>/<team>
        assert "-w" in cmd, (
            f"'-w' flag missing from documenter docker run cmd: {cmd}"
        )
        w_idx = cmd.index("-w")
        workdir = cmd[w_idx + 1]
        expected = "/data/workspaces/roboco-api/backend"
        assert workdir == expected, (
            f"Expected documenter workdir '{expected}' but got '{workdir}'. "
            f"Full cmd: {cmd}"
        )

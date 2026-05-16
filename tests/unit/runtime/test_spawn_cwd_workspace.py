"""Wave A2+A3 (2026-05-12): agent container cwd is set to the workspace path
so Edit(README.md) and git add README.md resolve inside the workspace clone.

Smoke run 3 showed Edit failing with 'Edit exists but is not enabled in this
context' and commit failing with 'outside repository at <workspace>' — both
caused by the container WORKDIR being /app (the roboco package) instead of
the agent's task workspace.
"""

from __future__ import annotations

import re
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


def _make_product_owner_config(
    *, project_slug: str = "roboco-api"
) -> OrchestratorAgentConfig:
    """Minimal AgentConfig for product-owner (product_owner role)."""
    return OrchestratorAgentConfig(
        agent_id="product-owner",
        blueprint_path=Path("/app/agents/blueprints/product-owner.md"),
        model="sonnet",
        mcp_config_path=Path("/app/mcp-config.json"),
        git_context=SpawnGitContext(project_slug=project_slug),
    )


def _make_head_marketing_config(
    *, project_slug: str = "roboco-api"
) -> OrchestratorAgentConfig:
    """Minimal AgentConfig for head-marketing (head_marketing role)."""
    return OrchestratorAgentConfig(
        agent_id="head-marketing",
        blueprint_path=Path("/app/agents/blueprints/head-marketing.md"),
        model="sonnet",
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


def _make_minimal_orchestrator() -> AgentOrchestrator:
    """Instantiate AgentOrchestrator with all constructor I/O mocked out."""
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    return orch


def _extract_workdir_from_cmd(cmd: list[str]) -> str | None:
    """Return the value after -w in a docker run cmd list, or None."""
    if "-w" not in cmd:
        return None
    return cmd[cmd.index("-w") + 1]


_EDIT_ALLOWLIST_RE = re.compile(r"^Edit\((.+)/\*\*\)$")


def _extract_edit_allowlist_prefix(permissions: dict[str, list[str]]) -> str:
    """Extract the workspace fs-path prefix from an Edit(path/**) rule.

    The rule MUST use the ``//`` absolute-filesystem form: Claude Code
    resolves a single leading ``/`` against the settings.json project
    root, not the container filesystem root, so a single-slash workspace
    allow silently never matches and Edit/Write are effectively denied
    (the smoke-10..14 "Edit not enabled in this context" failure). This
    asserts the ``//`` invariant and returns the real fs path (one
    leading slash) so it can be cross-checked against the docker ``-w``.

    Raises AssertionError if no matching entry is found.
    """
    for entry in permissions.get("allow", []):
        m = _EDIT_ALLOWLIST_RE.match(entry)
        if m:
            rule_path = m.group(1)
            assert rule_path.startswith("//"), (
                "Edit/Write allow rule must use the // absolute-filesystem "
                "form; a single leading / resolves against the settings.json "
                f"project root and silently never matches: {entry}"
            )
            return rule_path[1:]
    raise AssertionError(
        f"No Edit(<path>/**) entry found in allow list: {permissions['allow']}"
    )


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
            f"Expected workdir '{expected}' but got '{workdir}'. Full cmd: {cmd}"
        )

    def test_workdir_matches_edit_allowlist_path(self) -> None:
        """The -w value matches the Edit({workspace_path}/**) allowlist prefix.

        This test derives the expected path from _get_role_permissions, not
        from a hard-coded duplicate of the formula. If _build_mount_args and
        _get_role_permissions drift to different formulas, this test catches it.
        """
        project_slug = "my-project"
        # Workspace paths that _prepare_agent_spawn would compute for be-dev-1.
        # be-dev-1 resolves to team=backend (agents_config); we use the same
        # values the real code uses so the cross-check is meaningful.
        workspace_path = f"/data/workspaces/{project_slug}/backend/be-dev-1"
        cell_workspace_path = f"/data/workspaces/{project_slug}/backend"

        orch = _make_minimal_orchestrator()
        permissions = orch._get_role_permissions(
            role="developer",
            workspace_path=workspace_path,
            cell_workspace_path=cell_workspace_path,
        )
        edit_prefix = _extract_edit_allowlist_prefix(permissions)

        # Now build the docker cmd for the same agent/project.
        config = _make_dev_config(project_slug=project_slug)
        cmd = _build_cmd("roboco-agent-be-dev-1", config)

        workdir = _extract_workdir_from_cmd(cmd)
        assert workdir is not None, f"'-w' flag missing from docker run cmd: {cmd}"
        assert workdir == edit_prefix, (
            f"_build_mount_args -w value '{workdir}' does not match "
            f"_get_role_permissions Edit allowlist prefix '{edit_prefix}'. "
            "These two sites must use the same workspace-path formula."
        )


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
        assert "-w" in cmd, f"'-w' flag missing from documenter docker run cmd: {cmd}"
        w_idx = cmd.index("-w")
        workdir = cmd[w_idx + 1]
        expected = "/data/workspaces/roboco-api/backend"
        assert workdir == expected, (
            f"Expected documenter workdir '{expected}' but got '{workdir}'. "
            f"Full cmd: {cmd}"
        )


class TestProductOwnerSpawnCwdWorkspace:
    """product_owner container must start in the per-agent workspace path."""

    def test_cmd_contains_workdir_flag(self) -> None:
        """docker run for a product_owner includes -w <per-agent-workspace>."""
        config = _make_product_owner_config(project_slug="roboco-api")
        cmd = _build_cmd("roboco-agent-product-owner", config)

        assert "-w" in cmd, (
            f"'-w' flag missing from product_owner docker run cmd: {cmd}"
        )
        workdir = _extract_workdir_from_cmd(cmd)
        expected = "/data/workspaces/roboco-api/board/product-owner"
        assert workdir == expected, (
            f"Expected product_owner workdir '{expected}' but got '{workdir}'. "
            f"Full cmd: {cmd}"
        )

    def test_workdir_matches_edit_allowlist_path(self) -> None:
        """The product_owner -w value matches its Edit allowlist prefix."""
        project_slug = "roboco-api"
        workspace_path = f"/data/workspaces/{project_slug}/board/product-owner"
        cell_workspace_path = f"/data/workspaces/{project_slug}/board"

        orch = _make_minimal_orchestrator()
        permissions = orch._get_role_permissions(
            role="product_owner",
            workspace_path=workspace_path,
            cell_workspace_path=cell_workspace_path,
        )
        edit_prefix = _extract_edit_allowlist_prefix(permissions)

        config = _make_product_owner_config(project_slug=project_slug)
        cmd = _build_cmd("roboco-agent-product-owner", config)
        workdir = _extract_workdir_from_cmd(cmd)

        assert workdir is not None, (
            f"'-w' flag missing from product_owner docker run cmd: {cmd}"
        )
        assert workdir == edit_prefix, (
            f"_build_mount_args -w value '{workdir}' != "
            f"_get_role_permissions Edit prefix '{edit_prefix}'."
        )


class TestHeadMarketingSpawnCwdWorkspace:
    """head_marketing container must start in the per-agent workspace path."""

    def test_cmd_contains_workdir_flag(self) -> None:
        """docker run for a head_marketing includes -w <per-agent-workspace>."""
        config = _make_head_marketing_config(project_slug="roboco-api")
        cmd = _build_cmd("roboco-agent-head-marketing", config)

        assert "-w" in cmd, (
            f"'-w' flag missing from head_marketing docker run cmd: {cmd}"
        )
        workdir = _extract_workdir_from_cmd(cmd)
        expected = "/data/workspaces/roboco-api/board/head-marketing"
        assert workdir == expected, (
            f"Expected head_marketing workdir '{expected}' but got '{workdir}'. "
            f"Full cmd: {cmd}"
        )

    def test_workdir_matches_edit_allowlist_path(self) -> None:
        """The head_marketing -w value matches its Edit allowlist prefix."""
        project_slug = "roboco-api"
        workspace_path = f"/data/workspaces/{project_slug}/board/head-marketing"
        cell_workspace_path = f"/data/workspaces/{project_slug}/board"

        orch = _make_minimal_orchestrator()
        permissions = orch._get_role_permissions(
            role="head_marketing",
            workspace_path=workspace_path,
            cell_workspace_path=cell_workspace_path,
        )
        edit_prefix = _extract_edit_allowlist_prefix(permissions)

        config = _make_head_marketing_config(project_slug=project_slug)
        cmd = _build_cmd("roboco-agent-head-marketing", config)
        workdir = _extract_workdir_from_cmd(cmd)

        assert workdir is not None, (
            f"'-w' flag missing from head_marketing docker run cmd: {cmd}"
        )
        assert workdir == edit_prefix, (
            f"_build_mount_args -w value '{workdir}' != "
            f"_get_role_permissions Edit prefix '{edit_prefix}'."
        )

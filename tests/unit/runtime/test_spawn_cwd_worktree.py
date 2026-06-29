"""Spawn-side per-task worktree wiring (F123, Phase B — the atomic counterpart).

``create_branch`` now cuts a worktree at ``{clone_root}/.worktrees/{task-short}/``
instead of checking the branch out on the shared clone. The agent must be
POINTED at that worktree or it edits the clone root (parked on the default
branch) on the wrong branch. This pins: ``SpawnGitContext`` carries
``task_short_id``; ``_task_git_context`` populates it (branchless roots get
none); and the container ``-w`` + Edit/Write allowlist move to the worktree
IN LOCKSTEP (one formula) when a task short id is present, falling back to the
clone root otherwise.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from roboco.models.runtime import OrchestratorAgentConfig, SpawnGitContext
from roboco.runtime.orchestrator import (
    AgentOrchestrator,
    _agent_cwd_path,
    _agent_worktree_path,
)


def _make_dev_config(
    *,
    project_slug: str = "roboco-api",
    task_short_id: str | None = None,
    branch_name: str | None = "feature/backend/TASK0001",
) -> OrchestratorAgentConfig:
    return OrchestratorAgentConfig(
        agent_id="be-dev-1",
        blueprint_path=Path("/app/agents/blueprints/be-dev-1.md"),
        model="sonnet",
        mcp_config_path=Path("/app/mcp-config.json"),
        git_context=SpawnGitContext(
            project_slug=project_slug,
            branch_name=branch_name,
            task_short_id=task_short_id,
        ),
    )


def _minimal_hosts() -> dict[str, str | None]:
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
    return {
        "agent_tool_call_warn": 80,
        "agent_tool_call_halt": 100,
        "agent_loop_threshold": 5,
        "agent_loop_window": 10,
        "agent_stop_attempt_allowance": 2,
        "manifest_host_dir": "/tmp/manifests",
        "workspaces_root": "/data/workspaces",
    }


def _build_cmd(config: OrchestratorAgentConfig) -> list[str]:
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
        return AgentOrchestrator._build_mount_args(
            "roboco-agent-be-dev-1", config, hosts
        )


def _workdir(cmd: list[str]) -> str | None:
    if "-w" not in cmd:
        return None
    return cmd[cmd.index("-w") + 1]


def _make_minimal_orchestrator() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        return AgentOrchestrator.__new__(AgentOrchestrator)


class TestSpawnGitContextTaskShortId:
    def test_task_short_id_defaults_none(self) -> None:
        ctx = SpawnGitContext(project_slug="p", branch_name="b")
        assert ctx.task_short_id is None

    def test_task_short_id_round_trips(self) -> None:
        ctx = SpawnGitContext(
            project_slug="p", branch_name="b", task_short_id="a3c40fe7"
        )
        assert ctx.task_short_id == "a3c40fe7"


class TestAgentWorktreePath:
    def test_appends_worktrees_segment(self) -> None:
        assert (
            _agent_worktree_path("roboco-api", "backend", "be-dev-1", "a3c40fe7")
            == "/data/workspaces/roboco-api/backend/be-dev-1/.worktrees/a3c40fe7"
        )


class TestAgentCwdPath:
    def test_worktree_when_task_short_id_set(self) -> None:
        ctx = SpawnGitContext(
            project_slug="roboco-api",
            branch_name="feature/backend/TASK0001",
            task_short_id="a3c40fe7",
        )
        assert _agent_cwd_path("roboco-api", "backend", "be-dev-1", ctx) == (
            "/data/workspaces/roboco-api/backend/be-dev-1/.worktrees/a3c40fe7"
        )

    def test_clone_root_when_no_task_short_id(self) -> None:
        ctx = SpawnGitContext(
            project_slug="roboco-api", branch_name="feature/backend/TASK0001"
        )
        assert _agent_cwd_path("roboco-api", "backend", "be-dev-1", ctx) == (
            "/data/workspaces/roboco-api/backend/be-dev-1"
        )

    def test_clone_root_when_no_git_context(self) -> None:
        assert _agent_cwd_path("roboco-api", "backend", "be-dev-1", None) == (
            "/data/workspaces/roboco-api/backend/be-dev-1"
        )


class TestAppendWorkspaceCwdWorktree:
    def test_workdir_is_worktree_when_task_short_id_set(self) -> None:
        config = _make_dev_config(task_short_id="a3c40fe7")
        cmd = _build_cmd(config)
        wd = _workdir(cmd)
        assert wd == (
            "/data/workspaces/roboco-api/backend/be-dev-1/.worktrees/a3c40fe7"
        )

    def test_workdir_is_clone_root_when_no_task_short_id(self) -> None:
        config = _make_dev_config(task_short_id=None)
        cmd = _build_cmd(config)
        wd = _workdir(cmd)
        assert wd == "/data/workspaces/roboco-api/backend/be-dev-1"


class TestCwdMatchesEditAllowlistWorktree:
    """-w and the Edit/Write allowlist prefix must be the SAME path (lockstep)."""

    def test_worktree_path_matches_allowlist_prefix(self) -> None:
        project_slug = "roboco-api"
        cwd = _agent_cwd_path(
            project_slug,
            "backend",
            "be-dev-1",
            SpawnGitContext(
                project_slug=project_slug,
                branch_name="feature/backend/TASK0001",
                task_short_id="a3c40fe7",
            ),
        )
        cell = f"/data/workspaces/{project_slug}/backend"

        orch = _make_minimal_orchestrator()
        permissions = orch._get_role_permissions(
            role="developer", workspace_path=cwd, cell_workspace_path=cell
        )

        # The Edit allow rule is Edit(//<cwd>/**); strip the leading slash
        # added by _get_role_permissions to compare against cwd.
        edit_rules = [r for r in permissions["allow"] if r.startswith("Edit(//")]
        assert edit_rules, f"no Edit(//...) allow rule: {permissions['allow']}"
        rule_path = edit_rules[0][len("Edit(/") : -4]  # drop "Edit(/" and "/**)"
        assert rule_path == cwd, (
            f"Edit allowlist prefix '{rule_path}' != cwd '{cwd}'; the -w flag "
            "and the Edit/Write scope must point at the same worktree path."
        )

        # And the docker -w must equal the same cwd.
        config = _make_dev_config(task_short_id="a3c40fe7")
        cmd = _build_cmd(config)
        assert _workdir(cmd) == cwd


class TestTaskGitContextTaskShortId:
    def _orch(self) -> AgentOrchestrator:
        return _make_minimal_orchestrator()

    def test_populates_task_short_id_when_branch_present(self) -> None:
        orch = self._orch()
        task_id = "a3c40fe7-0000-0000-0000-000000000000"
        ctx = orch._task_git_context(
            {
                "project_slug": "roboco-api",
                "branch_name": "feature/backend/abc12345",
                "id": task_id,
            }
        )
        assert ctx is not None
        assert ctx.task_short_id == "a3c40fe7"
        assert ctx.branch_name == "feature/backend/abc12345"

    def test_no_task_short_id_for_branchless_root(self) -> None:
        # A branchless coordination root (umbrella / no-project product root)
        # has no worktree — task_short_id must stay None so the spawn cwd
        # falls back to the clone root, not a phantom .worktrees/<id> dir.
        orch = self._orch()
        ctx = orch._task_git_context(
            {"project_slug": "roboco-api", "branch_name": None, "id": "abc12345"}
        )
        assert ctx is not None
        assert ctx.task_short_id is None

    def test_returns_none_without_project_slug(self) -> None:
        orch = self._orch()
        ctx = orch._task_git_context({"branch_name": "b", "id": "abc12345"})
        assert ctx is None

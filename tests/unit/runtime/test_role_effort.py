"""Per-role effort: Claude Code's `--effort <level>` flag is added to the spawn
argv only for roles present in ROLE_EFFORT_MAP."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from roboco.models.runtime import (
    ROLE_EFFORT_MAP,
    OrchestratorAgentConfig,
    SpawnGitContext,
)
from roboco.runtime.orchestrator import AgentOrchestrator


def _config(agent_id: str) -> OrchestratorAgentConfig:
    return OrchestratorAgentConfig(
        agent_id=agent_id,
        blueprint_path=Path(f"/app/agents/blueprints/{agent_id}.md"),
        model="sonnet",
        mcp_config_path=Path("/app/mcp-config.json"),
        git_context=SpawnGitContext(
            project_slug="roboco-api",
            branch_name="feature/backend/TASK0001",
        ),
    )


def _spawn_args(agent_id: str) -> list[str]:
    cmd: list[str] = []
    with patch(
        "roboco.runtime.orchestrator._resolve_agent_cli_model",
        return_value="claude-sonnet-5",
    ):
        AgentOrchestrator._append_image_and_claude_args(cmd, _config(agent_id), None)
    return cmd


def test_effort_flag_injected_for_mapped_role() -> None:
    with patch("roboco.runtime.orchestrator.ROLE_EFFORT_MAP", {"developer": "low"}):
        args = _spawn_args("be-dev-1")  # be-dev-1 → developer
    assert "--effort" in args
    assert args[args.index("--effort") + 1] == "low"


def test_no_effort_flag_for_unmapped_role() -> None:
    with patch("roboco.runtime.orchestrator.ROLE_EFFORT_MAP", {"cell_pm": "medium"}):
        args = _spawn_args("be-dev-1")  # developer — not in the patched map
    assert "--effort" not in args


def test_shipped_map_sets_cell_pm_to_medium() -> None:
    # The shipped ROLE_EFFORT_MAP routes cell_pm to medium.
    assert ROLE_EFFORT_MAP.get("cell_pm") == "medium"
    args = _spawn_args("be-pm")  # be-pm → cell_pm
    assert "--effort" in args
    assert args[args.index("--effort") + 1] == "medium"

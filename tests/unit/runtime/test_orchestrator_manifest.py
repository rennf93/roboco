"""Tests for orchestrator spawn-manifest mounting (Phase 1)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

from roboco.agents_config import ALL_AGENTS, get_agent_role
from roboco.runtime.orchestrator import GATEWAY_ENABLED_ROLES, _build_manifest_for_agent
from roboco.seeds.initial_data import AGENT_UUIDS

if TYPE_CHECKING:
    from pathlib import Path

# Roles the orchestrator never spawns as containerized delivery agents, so they
# legitimately get no spawn manifest: the human-only chat agents (prompter,
# secretary), the human CEO, and the orchestrator's own `system` sentinel. Every
# OTHER seeded role must be gateway-enabled or it boots with no flow verbs.
NON_SPAWNED_ROLES = {"prompter", "secretary", "ceo", "system"}


class TestGatewayEnabledRoles:
    def test_all_roles_enabled(self) -> None:
        """Phase 4: every role gets the gateway manifest."""
        assert "developer" in GATEWAY_ENABLED_ROLES
        assert "qa" in GATEWAY_ENABLED_ROLES
        assert "documenter" in GATEWAY_ENABLED_ROLES
        assert "cell_pm" in GATEWAY_ENABLED_ROLES
        assert "main_pm" in GATEWAY_ENABLED_ROLES

    def test_board_roles_enabled_in_phase4(self) -> None:
        """Phase 4: board roles included."""
        assert "product_owner" in GATEWAY_ENABLED_ROLES
        assert "head_marketing" in GATEWAY_ENABLED_ROLES
        assert "auditor" in GATEWAY_ENABLED_ROLES

    def test_pr_reviewer_enabled(self) -> None:
        """The PR reviewer is a spawned delivery agent — it must be enabled.

        Without this it gets ROBOCO_GATEWAY_ENABLED=false and no manifest, so
        none of its flow verbs (claim_pr_review/post_pr_review) are registered;
        it can never claim its task and the dispatcher respawns it forever.
        """
        assert "pr_reviewer" in GATEWAY_ENABLED_ROLES

    def test_every_spawnable_agent_role_is_gateway_enabled(self) -> None:
        """Invariant: every seeded agent the orchestrator spawns has a manifest.

        Guards against the regression where a new spawnable role is added to the
        roster but forgotten here — that role would spawn with no flow verbs and
        loop. Only the never-spawned roles (prompter/secretary/ceo/system) may be
        absent.
        """
        missing = {
            agent_id: role
            for agent_id in ALL_AGENTS
            if (role := get_agent_role(agent_id)) not in NON_SPAWNED_ROLES
            and role not in GATEWAY_ENABLED_ROLES
        }
        assert not missing, (
            f"spawnable agents missing from GATEWAY_ENABLED_ROLES "
            f"(would loop with no flow verbs): {missing}"
        )


class TestBuildManifestForAgent:
    def test_developer_writes_file(self, tmp_path: Path) -> None:
        """Developer role produces a manifest JSON file at the expected host path."""
        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(tmp_path)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("be-dev-1", "claude-sonnet-4-6")

        assert result is not None
        assert result.exists()
        assert result.name == "be-dev-1.json"
        data = json.loads(result.read_text())
        assert data["role"] == "developer"
        assert data["team"] == "backend"

    def test_developer_returns_path_inside_manifest_host_dir(
        self, tmp_path: Path
    ) -> None:
        """The returned path is inside manifest_host_dir."""
        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(tmp_path)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("fe-dev-2", "claude-sonnet-4-6")

        assert result is not None
        assert result.parent == tmp_path

    def test_developer_manifest_content_valid(self, tmp_path: Path) -> None:
        """Written manifest has required top-level keys."""
        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(tmp_path)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("be-dev-1", "claude-opus-4-6")

        assert result is not None
        data = json.loads(result.read_text())
        for key in (
            "agent_id",
            "role",
            "team",
            "workspace_path",
            "flow_tools",
            "do_tools",
            "bash_allowed",
        ):
            assert key in data, f"missing key: {key}"

    def test_developer_agent_id_in_manifest_matches_seed_uuid(
        self, tmp_path: Path
    ) -> None:
        """agent_id field in the manifest matches the seeded UUID for be-dev-1."""
        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(tmp_path)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("be-dev-1", "claude-sonnet-4-6")

        assert result is not None
        data = json.loads(result.read_text())
        assert data["agent_id"] == AGENT_UUIDS["be-dev-1"]

    def test_qa_writes_file(self, tmp_path: Path) -> None:
        """Phase 2: QA role now produces a manifest JSON file (same as developer)."""
        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(tmp_path)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("be-qa", "claude-sonnet-4-6")

        assert result is not None
        assert result.exists()
        assert result.name == "be-qa.json"
        data = json.loads(result.read_text())
        assert data["role"] == "qa"
        assert data["team"] == "backend"

    def test_documenter_writes_file(self, tmp_path: Path) -> None:
        """Phase 3: Documenter role produces a manifest JSON file."""
        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(tmp_path)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("be-doc", "claude-haiku-4-5-20251001")

        assert result is not None
        assert result.exists()
        assert result.name == "be-doc.json"
        data = json.loads(result.read_text())
        assert data["role"] == "documenter"
        assert data["team"] == "backend"

    def test_cell_pm_writes_file(self, tmp_path: Path) -> None:
        """Phase 3: Cell PM role produces a manifest JSON file."""
        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(tmp_path)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("be-pm", "claude-sonnet-4-6")

        assert result is not None
        assert result.exists()
        assert result.name == "be-pm.json"
        data = json.loads(result.read_text())
        assert data["role"] == "cell_pm"
        assert data["team"] == "backend"

    def test_main_pm_writes_file(self, tmp_path: Path) -> None:
        """Phase 3: Main PM role produces a manifest JSON file."""
        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(tmp_path)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("main-pm", "claude-sonnet-4-6")

        assert result is not None
        assert result.exists()
        assert result.name == "main-pm.json"
        data = json.loads(result.read_text())
        assert data["role"] == "main_pm"

    def test_pr_reviewer_writes_file_with_review_verbs(self, tmp_path: Path) -> None:
        """pr-reviewer-1 produces a manifest carrying its review flow verbs.

        Regression guard for the respawn loop: if this returns None (role not
        gateway-enabled) the reviewer spawns with no task tools.
        """
        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(tmp_path)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("pr-reviewer-1", "claude-sonnet-4-6")

        assert result is not None, "pr-reviewer-1 must produce a manifest"
        assert result.exists()
        assert result.name == "pr-reviewer-1.json"
        data = json.loads(result.read_text())
        assert data["role"] == "pr_reviewer"
        assert "claim_pr_review" in data["flow_tools"]
        assert "post_pr_review" in data["flow_tools"]

    def test_manifest_dir_created_if_absent(self, tmp_path: Path) -> None:
        """manifest_host_dir is created automatically when it doesn't exist."""
        nested = tmp_path / "new" / "nested" / "dir"
        assert not nested.exists()

        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(nested)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("be-dev-1", "claude-sonnet-4-6")

        assert result is not None
        assert nested.exists()
        assert result.exists()

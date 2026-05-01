"""Tests for orchestrator spawn-manifest mounting (Phase 1)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

from roboco.runtime.orchestrator import GATEWAY_ENABLED_ROLES, _build_manifest_for_agent
from roboco.seeds.initial_data import AGENT_UUIDS

if TYPE_CHECKING:
    from pathlib import Path


class TestGatewayEnabledRoles:
    def test_developer_in_set(self) -> None:
        """GATEWAY_ENABLED_ROLES must contain developer in Phase 1."""
        assert "developer" in GATEWAY_ENABLED_ROLES

    def test_qa_not_in_set(self) -> None:
        assert "qa" not in GATEWAY_ENABLED_ROLES

    def test_cell_pm_not_in_set(self) -> None:
        assert "cell_pm" not in GATEWAY_ENABLED_ROLES

    def test_documenter_not_in_set(self) -> None:
        assert "documenter" not in GATEWAY_ENABLED_ROLES

    def test_main_pm_not_in_set(self) -> None:
        assert "main_pm" not in GATEWAY_ENABLED_ROLES


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

    def test_qa_returns_none(self, tmp_path: Path) -> None:
        """QA role is outside GATEWAY_ENABLED_ROLES — returns None, no file written."""
        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(tmp_path)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("be-qa", "claude-sonnet-4-6")

        assert result is None
        assert not list(tmp_path.glob("*.json"))

    def test_cell_pm_returns_none(self, tmp_path: Path) -> None:
        """Cell PM role is outside GATEWAY_ENABLED_ROLES — returns None."""
        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(tmp_path)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("be-pm", "claude-sonnet-4-6")

        assert result is None

    def test_documenter_returns_none(self, tmp_path: Path) -> None:
        """Documenter role is outside GATEWAY_ENABLED_ROLES — returns None."""
        with patch("roboco.runtime.orchestrator.settings") as mock_settings:
            mock_settings.manifest_host_dir = str(tmp_path)
            mock_settings.workspaces_root = str(tmp_path / "workspaces")

            result = _build_manifest_for_agent("be-doc", "claude-haiku-4-5-20251001")

        assert result is None

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

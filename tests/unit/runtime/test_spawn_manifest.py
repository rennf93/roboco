"""Tests for per-role spawn manifest construction."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from roboco.runtime.spawn_manifest import SpawnInputs, build_for_role, write_manifest


class TestBuildForRole:
    def test_developer_manifest(self) -> None:
        m = build_for_role(
            SpawnInputs(
                agent_id=uuid4(),
                role="developer",
                team="backend",
                workspace_path=Path("/data/workspaces/roboco/backend/be-dev-1"),
                agent_model="minimax-m3:cloud",
            )
        )
        assert "i_am_done" in m.flow_tools
        assert "commit" in m.do_tools
        # Issue #8: the developer manifest must carry `evidence` so the
        # do-server registers mcp__roboco-do__evidence inside the container.
        assert "evidence" in m.do_tools
        assert "Edit" in m.write_tools
        assert m.subagent_allowed is False
        assert m.subagent_model is None  # devs don't dispatch
        assert m.bash_allowed is True
        assert "ROBOCO_SDK_URL" in m.env or "ROBOCO_PUBLIC_BASE_URL" in m.env

    def test_main_pm_manifest_subagent_uses_parent_model(self) -> None:
        m = build_for_role(
            SpawnInputs(
                agent_id=uuid4(),
                role="main_pm",
                team="board",
                workspace_path=Path("/data/workspaces/roboco/board/main-pm"),
                agent_model="minimax-m3:cloud",
            )
        )
        assert m.subagent_allowed is True
        assert m.subagent_model == "minimax-m3:cloud"

    def test_qa_manifest_no_write(self) -> None:
        m = build_for_role(
            SpawnInputs(
                agent_id=uuid4(),
                role="qa",
                team="backend",
                workspace_path=Path("/data/workspaces/roboco/backend/be-qa"),
                agent_model="minimax-m3:cloud",
            )
        )
        assert m.write_tools == []

    def test_unknown_role_raises(self) -> None:
        with pytest.raises(KeyError):
            build_for_role(
                SpawnInputs(
                    agent_id=uuid4(),
                    role="unknown",
                    team="x",
                    workspace_path=Path("/tmp/x"),
                    agent_model="x",
                )
            )


class TestWriteManifest:
    def test_writes_json(self, tmp_path: Path) -> None:
        m = build_for_role(
            SpawnInputs(
                agent_id=uuid4(),
                role="developer",
                team="backend",
                workspace_path=tmp_path,
                agent_model="claude-opus-4-7",
            )
        )
        manifest_path = tmp_path / "tool-manifest.json"
        write_manifest(m, manifest_path)
        data = json.loads(manifest_path.read_text())
        assert data["role"] == "developer"
        assert "i_am_done" in data["flow_tools"]
        assert data["bash_allowed"] is True

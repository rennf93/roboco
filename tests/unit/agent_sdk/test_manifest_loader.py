"""Tests for agent_sdk.server.load_tool_manifest().

load_tool_manifest() reads its env variables at call-time, so monkeypatch
is enough — no importlib.reload required.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import roboco.agent_sdk.server as srv

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_manifest_loader_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Returns None when ROBOCO_GATEWAY_ENABLED is false, even if file exists."""
    monkeypatch.setenv("ROBOCO_GATEWAY_ENABLED", "false")
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps({"flow_tools": ["x"]}))
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_file))

    assert srv.load_tool_manifest() is None


def test_manifest_loader_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Returns parsed manifest dict when gateway is enabled and file is valid."""
    monkeypatch.setenv("ROBOCO_GATEWAY_ENABLED", "true")
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps({"flow_tools": ["i_am_done"]}))
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_file))

    result = srv.load_tool_manifest()

    assert result is not None
    flow_tools = result["flow_tools"]
    assert isinstance(flow_tools, list)
    assert "i_am_done" in flow_tools


def test_manifest_loader_enabled_file_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Returns None (not an error) when gateway is enabled but file is absent."""
    monkeypatch.setenv("ROBOCO_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(tmp_path / "no-such-file.json"))

    assert srv.load_tool_manifest() is None


def test_manifest_loader_enabled_invalid_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Returns None (not an error) when gateway is enabled but JSON is invalid."""
    monkeypatch.setenv("ROBOCO_GATEWAY_ENABLED", "true")
    manifest_file = tmp_path / "bad.json"
    manifest_file.write_text("{ not valid json }")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_file))

    assert srv.load_tool_manifest() is None

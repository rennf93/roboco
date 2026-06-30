"""#162: ``_register_tools`` raises on a missing manifest — dev/test override.

In production the manifest is the role-authoritative tool list and a missing
one must fail loud (an agent without its manifest would see off-role verbs).
But dev/test imports of the server modules need an escape hatch that does not
require hand-writing a manifest file. ``ROBOCO_ALLOW_FULL_TOOLSET`` lets a
missing manifest fall back to registering the full tool set instead of
raising — default-off so production behaviour is unchanged.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


def _seed_no_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000099")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    # Point at a path that does not exist — no manifest.
    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(missing))
    monkeypatch.delenv("ROBOCO_ALLOW_FULL_TOOLSET", raising=False)
    return missing


def test_flow_missing_manifest_raises_without_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed_no_manifest(monkeypatch, tmp_path)
    import roboco.mcp.flow_server as srv

    with pytest.raises(RuntimeError, match="manifest unavailable"):
        importlib.reload(srv)


def test_flow_missing_manifest_falls_back_with_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed_no_manifest(monkeypatch, tmp_path)
    monkeypatch.setenv("ROBOCO_ALLOW_FULL_TOOLSET", "1")
    import roboco.mcp.flow_server as srv

    importlib.reload(srv)
    # Full tool set registered — every flow tool is on the palette.
    assert set(srv._REGISTERED_TOOLS) == set(srv._TOOLS.keys())


def test_do_missing_manifest_raises_without_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed_no_manifest(monkeypatch, tmp_path)
    import roboco.mcp.do_server as srv

    with pytest.raises(RuntimeError, match="manifest unavailable"):
        importlib.reload(srv)


def test_do_missing_manifest_falls_back_with_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed_no_manifest(monkeypatch, tmp_path)
    monkeypatch.setenv("ROBOCO_ALLOW_FULL_TOOLSET", "1")
    import roboco.mcp.do_server as srv

    importlib.reload(srv)
    assert set(srv._REGISTERED_TOOLS) == set(srv._TOOLS.keys())

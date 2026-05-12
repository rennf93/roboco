"""Wave C1: per-verb circuit breaker trips on incomplete_input too.

Smoke run 3 showed Main PM hitting 7 incomplete_input rejections in
~30 seconds on the decision-note required-fields gate. The breaker
only included tracing_gap; incomplete_input was added in Wave 1 but
the breaker classification wasn't updated.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import types
    from pathlib import Path


_MINIMAL_MANIFEST = {
    "agent_id": "00000000-0000-0000-0000-000000000042",
    "role": "main_pm",
    "team": "management",
    "workspace_path": "/tmp/test",
    "flow_tools": ["i_will_plan", "triage_all", "unblock", "complete", "i_am_idle"],
    "do_tools": [],
    "read_tools": [],
    "write_tools": [],
    "bash_allowed": False,
    "subagent_allowed": False,
    "subagent_model": None,
    "env": {},
}


@pytest.fixture()
def flow_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    """Import flow_server with minimal env vars needed for constant inspection."""
    manifest_path = tmp_path / "tool-manifest.json"
    manifest_path.write_text(json.dumps(_MINIMAL_MANIFEST))

    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000042")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "main_pm")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_SDK_URL", "http://test-sdk:9000")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_path))

    import roboco.mcp.flow_server as srv

    importlib.reload(srv)
    return srv


def test_circuit_kinds_include_incomplete_input(
    flow_module: types.ModuleType,
) -> None:
    """incomplete_input is in the breaker's rejection-kind set."""
    assert "incomplete_input" in flow_module._CIRCUIT_REJECTION_KINDS


def test_circuit_kinds_include_tracing_gap(
    flow_module: types.ModuleType,
) -> None:
    """Regression: tracing_gap stays in the set (the original kind)."""
    assert "tracing_gap" in flow_module._CIRCUIT_REJECTION_KINDS

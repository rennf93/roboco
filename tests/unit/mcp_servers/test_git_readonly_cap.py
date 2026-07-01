"""roboco_git_diff caps oversized diff text at the MCP boundary.

The HTTP route stays uncapped (the panel diff viewer reads it whole); the
truncation happens only on the agent-facing tool result so a huge diff can't
flood the session context.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import types


@pytest.fixture
def git_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000042")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    import roboco.mcp.git_readonly as srv

    importlib.reload(srv)
    return srv


def test_cap_diff_truncates_and_annotates(git_module: types.ModuleType) -> None:
    big = git_module._cap_diff({"diff": "z" * (git_module._DIFF_CAP_CHARS + 100)})
    assert big["diff_truncated"] is True
    assert "diff truncated" in big["diff"]
    assert len(big["diff"]) < git_module._DIFF_CAP_CHARS + 300


def test_cap_diff_passes_small_untouched(git_module: types.ModuleType) -> None:
    small = git_module._cap_diff({"diff": "tiny"})
    assert small["diff"] == "tiny"
    assert "diff_truncated" not in small
    missing = git_module._cap_diff({"files_changed": 0})
    assert "diff_truncated" not in missing

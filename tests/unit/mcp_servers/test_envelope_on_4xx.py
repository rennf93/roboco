"""P0-6 / D-13: MCP _post() surfaces envelope body on 4xx.

The pre-fix path called ``response.raise_for_status()`` then ``.json()``,
which discarded the body on any 4xx — agents saw a Python
``httpx.HTTPStatusError`` traceback instead of the orchestrator's
``{error, message, remediate, missing}`` envelope. These tests pin the
fixed contract: 2xx and 4xx both return the parsed JSON; only an
unparseable body produces a synthetic ``transport_error`` envelope.
"""

from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    import types


_MANIFEST = {
    "agent_id": "00000000-0000-0000-0000-000000000001",
    "role": "developer",
    "team": "backend",
    "workspace_path": "/tmp/test",
    "flow_tools": ["give_me_work", "i_will_work_on", "i_am_done"],
    "do_tools": ["commit", "note"],
    "read_tools": [],
    "write_tools": [],
    "bash_allowed": True,
    "subagent_allowed": False,
    "subagent_model": None,
    "env": {},
}


def _seed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = Path(tempfile.mkdtemp()) / "tool-manifest.json"
    manifest_path.write_text(json.dumps(_MANIFEST))
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_path))


def _fake_client_with(status: int, body: Any) -> MagicMock:
    """httpx.Client context-manager whose post() returns the given response."""
    fake_response = MagicMock()
    fake_response.status_code = status
    if isinstance(body, dict):
        fake_response.json.return_value = body
    else:
        fake_response.json.side_effect = ValueError("not json")
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.post.return_value = fake_response
    return fake_client


@pytest.fixture
def flow_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    _seed_env(monkeypatch)
    import roboco.mcp.flow_server as srv

    importlib.reload(srv)
    return srv


@pytest.fixture
def do_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    _seed_env(monkeypatch)
    import roboco.mcp.do_server as srv

    importlib.reload(srv)
    return srv


def test_flow_post_returns_envelope_on_422(flow_module: types.ModuleType) -> None:
    """422 with envelope body must surface as the envelope, not raise."""
    envelope_body = {
        "error": "tracing_gap",
        "message": "missing plan",
        "remediate": "call i_will_work_on(task_id=..., plan='...')",
        "missing": ["plan"],
    }
    client = _fake_client_with(422, envelope_body)
    with patch("httpx.Client", return_value=client):
        result = flow_module.give_me_work()
    assert result == envelope_body


def test_flow_post_returns_envelope_on_400(flow_module: types.ModuleType) -> None:
    """400 with envelope body (e.g. role-gate rejection from do_server)."""
    body = {
        "error": "not_authorized",
        "message": "role 'cell_pm' may not commit code",
        "remediate": "PMs delegate; use delegate(...)",
        "missing": [],
    }
    client = _fake_client_with(400, body)
    with patch("httpx.Client", return_value=client):
        result = flow_module.i_will_work_on("task-id", plan="x")
    assert result["error"] == "not_authorized"


def test_flow_post_returns_envelope_on_404(flow_module: types.ModuleType) -> None:
    """Even 404 must surface body; only the body's content matters to the agent."""
    body = {
        "error": "not_found",
        "message": "task abc not found",
        "remediate": "call give_me_work() to find an actionable task",
        "missing": [],
    }
    client = _fake_client_with(404, body)
    with patch("httpx.Client", return_value=client):
        result = flow_module.i_am_done("task-abc", notes="done")
    assert result["error"] == "not_found"


def test_flow_post_synthesizes_transport_error_when_body_unparseable(
    flow_module: types.ModuleType,
) -> None:
    """No JSON body → synthetic transport_error envelope (NOT a raise)."""
    client = _fake_client_with(502, body=None)  # body=None → ValueError on .json()
    with patch("httpx.Client", return_value=client):
        result = flow_module.give_me_work()
    assert result["error"] == "transport_error"
    assert "502" in result["message"]
    assert "remediate" in result


def test_do_post_returns_envelope_on_400(do_module: types.ModuleType) -> None:
    """do_server mirrors flow_server: envelope surfaces on rejection."""
    body = {
        "error": "not_authorized",
        "message": "role 'cell_pm' may not commit",
        "remediate": "PMs delegate via delegate()",
        "missing": [],
    }
    client = _fake_client_with(400, body)
    with patch("httpx.Client", return_value=client):
        result = do_module.commit("any message")
    assert result["error"] == "not_authorized"

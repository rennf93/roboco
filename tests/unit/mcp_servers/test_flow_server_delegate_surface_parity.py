"""The delegate MCP tool must be able to send every field the gate demands.

Original bug (2026-07-02 live): TASK_AT_DELEGATE required ``intends_to_touch``
on code delegations, but the MCP ``delegate`` tool had no such parameter —
PMs were rejected 4x with ``incomplete_input``, could never comply, and
blocked/escalated. Fleet-wide code-delegation wall.

Invariant: every FieldRequirement in TASK_AT_DELEGATE (and TASK_AT_CREATE,
which it extends) is either a parameter of the MCP delegate tool or
server-resolved (never demanded from the caller).
"""

from __future__ import annotations

import importlib
import inspect
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from roboco.foundation.policy.task_completeness import TASK_AT_DELEGATE

if TYPE_CHECKING:
    import types
    from pathlib import Path

# Fields the choreographer resolves server-side; the tool never sends them.
_SERVER_RESOLVED = {"project_id"}


def _pm_manifest() -> dict[str, object]:
    return {
        "agent_id": "00000000-0000-0000-0000-000000000098",
        "role": "cell_pm",
        "team": "frontend",
        "workspace_path": "/tmp/test",
        "flow_tools": ["delegate", "i_am_idle"],
        "do_tools": [],
        "read_tools": [],
        "write_tools": [],
        "bash_allowed": True,
        "subagent_allowed": False,
        "subagent_model": None,
        "env": {},
    }


@pytest.fixture()
def flow_module_pm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    manifest_path = tmp_path / "tool-manifest.json"
    manifest_path.write_text(json.dumps(_pm_manifest()))
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000098")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "cell_pm")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_SDK_URL", "http://test-sdk:9000")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_path))

    import roboco.mcp.flow_server as srv

    importlib.reload(srv)
    return srv


def test_delegate_tool_covers_every_gate_required_field(
    flow_module_pm: types.ModuleType,
) -> None:
    """Every TASK_AT_DELEGATE FieldRequirement is a delegate() parameter."""
    params = set(inspect.signature(flow_module_pm.delegate).parameters)
    required = {req.field for req in TASK_AT_DELEGATE.requires}
    missing = required - params - _SERVER_RESOLVED
    assert not missing, (
        f"TASK_AT_DELEGATE demands fields the MCP delegate tool cannot send: "
        f"{sorted(missing)}. A PM rejected with incomplete_input for these "
        f"can NEVER comply — add them to flow_server.delegate and forward "
        f"them in the payload."
    )


def test_delegate_forwards_collision_surface_in_payload(
    flow_module_pm: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The surface fields actually reach the POST body (not just the signature)."""
    captured: dict[str, Any] = {}

    def _client_factory(*_a: object, **_kw: object) -> MagicMock:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        def _post(url: str, **kwargs: object) -> MagicMock:
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"status": "ok"}
            return resp

        client.post = _post
        return client

    monkeypatch.setattr(flow_module_pm.httpx, "Client", _client_factory)
    flow_module_pm.delegate(
        parent_task_id="00000000-0000-0000-0000-000000000001",
        title="t",
        description="a description well over twenty chars",
        assigned_to="fe-dev-1",
        team="frontend",
        task_type="code",
        nature="technical",
        acceptance_criteria=["done"],
        intends_to_touch=["frontend/src/components/behavioral-content.tsx"],
        adds_migration=False,
        touches_shared=True,
        depends_on=["00000000-0000-0000-0000-000000000002"],
    )
    body = captured["json"]
    assert body["intends_to_touch"] == [
        "frontend/src/components/behavioral-content.tsx"
    ]
    assert body["adds_migration"] is False
    assert body["touches_shared"] is True
    assert body["depends_on"] == ["00000000-0000-0000-0000-000000000002"]

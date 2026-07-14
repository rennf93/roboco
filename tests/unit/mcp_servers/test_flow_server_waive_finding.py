"""flow_server exposes the auditor's ``waive_finding`` tool.

The verb is auto-derived into the auditor manifest via
``intents_for_role(Role.AUDITOR)``; this pins that the MCP layer registers
it under the public name and POSTs the right payload to the auditor path.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from roboco.foundation.identity import Role
from roboco.foundation.policy.lifecycle import intents_for_role

if TYPE_CHECKING:
    import types
    from pathlib import Path


def _auditor_manifest() -> dict[str, object]:
    return {
        "agent_id": "00000000-0000-0000-0000-000000000004",
        "role": "auditor",
        "team": "board",
        "workspace_path": "/tmp/test",
        "flow_tools": list(intents_for_role(Role.AUDITOR)),
        "do_tools": [],
        "read_tools": [],
        "write_tools": [],
        "bash_allowed": True,
        "subagent_allowed": False,
        "subagent_model": None,
        "env": {},
    }


@pytest.fixture()
def flow_module_auditor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> types.ModuleType:
    manifest_path = tmp_path / "tool-manifest.json"
    manifest_path.write_text(json.dumps(_auditor_manifest()))
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000004")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "auditor")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_SDK_URL", "http://test-sdk:9000")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_path))
    import roboco.mcp.flow_server as srv

    importlib.reload(srv)
    return srv


def test_waive_finding_registers_for_auditor_manifest(
    flow_module_auditor: types.ModuleType,
) -> None:
    registered = flow_module_auditor._register_tools()
    assert "waive_finding" in registered, (
        f"waive_finding not registered for auditor manifest. "
        f"Registered: {sorted(registered)}"
    )


def test_waive_finding_posts_to_auditor_path(
    flow_module_auditor: types.ModuleType,
) -> None:
    captured: list[tuple[str, Any]] = []

    def _client_factory(*_a: object, **_kw: object) -> MagicMock:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        def _post(url: str, **kwargs: object) -> MagicMock:
            captured.append((url, kwargs.get("json", {})))
            resp = MagicMock()
            resp.json.return_value = {"status": "waived", "error": None}
            return resp

        client.post.side_effect = _post
        return client

    finding_id = "11111111-1111-1111-1111-111111111111"
    with patch("httpx.Client", side_effect=_client_factory):
        result = flow_module_auditor.waive_finding(finding_id, "cosmetic nit")

    assert result["status"] == "waived"
    orch_calls = [(u, b) for u, b in captured if "test-orchestrator" in u]
    assert len(orch_calls) == 1
    url, body = orch_calls[0]
    assert url.endswith("/api/v1/flow/auditor/waive_finding"), (
        f"waive_finding must POST to /auditor/waive_finding, got {url}"
    )
    assert body == {"finding_id": finding_id, "note": "cosmetic nit"}

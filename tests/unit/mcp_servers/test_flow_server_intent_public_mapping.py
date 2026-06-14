"""Smoke-7: flow_server maps IntentSpec verb names to public MCP tool names.

`pass`/`fail` are Python keywords so the IntentSpec layer (foundation) names
them `pass_review`/`fail_review`. The MCP layer must expose them under the
public names — agents/prompts say `pass(task_id, notes)`, not `pass_review`.

Original bug: the manifest carried `pass_review` (from `intents_for_role`)
but `_TOOLS` dict had key `pass`. The mismatch silently dropped both verbs
from registration. QA's smoke-7 run loop-spammed `dm`/`say` because the
`pass` tool didn't exist.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    import types
    from pathlib import Path


def _qa_manifest() -> dict[str, object]:
    """A minimal QA manifest using IntentSpec names (mirrors prod)."""
    return {
        "agent_id": "00000000-0000-0000-0000-000000000099",
        "role": "qa",
        "team": "backend",
        "workspace_path": "/tmp/test",
        # These are exactly what intents_for_role(Role.QA) produces in prod.
        "flow_tools": [
            "claim_review",
            "pass_review",
            "fail_review",
            "give_me_work",
            "i_am_blocked",
            "i_am_idle",
            "unclaim",
            "resume",
        ],
        "do_tools": [],
        "read_tools": [],
        "write_tools": [],
        "bash_allowed": True,
        "subagent_allowed": False,
        "subagent_model": None,
        "env": {},
    }


@pytest.fixture()
def flow_module_qa(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    """Import flow_server with a QA manifest mounted at the expected path."""
    manifest_path = tmp_path / "tool-manifest.json"
    manifest_path.write_text(json.dumps(_qa_manifest()))

    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000099")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "qa")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_SDK_URL", "http://test-sdk:9000")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_path))

    import roboco.mcp.flow_server as srv

    importlib.reload(srv)
    return srv


def test_pass_and_fail_register_under_public_names(
    flow_module_qa: types.ModuleType,
) -> None:
    """The manifest's pass_review/fail_review register as MCP tools 'pass'/'fail'."""
    registered = flow_module_qa._register_tools()
    assert "pass" in registered, (
        f"public name 'pass' not registered. Registered: {sorted(registered)}. "
        "The intent-to-public mapping is broken — QA cannot transition tasks."
    )
    assert "fail" in registered
    # IntentSpec names must NOT be exposed directly as MCP tool names.
    assert "pass_review" not in registered
    assert "fail_review" not in registered


def test_intent_public_mapping_used_on_unknowns(
    flow_module_qa: types.ModuleType,
) -> None:
    """Unmapped verbs not in _TOOLS surface as unknown; mapped ones don't."""
    # pass_review and fail_review must NOT appear in the 'unknown' warning log
    # because they map to pass/fail which ARE in _TOOLS.
    public_map = flow_module_qa._INTENT_TO_PUBLIC
    assert public_map["pass_review"] == "pass"
    assert public_map["fail_review"] == "fail"


def test_post_to_correct_orchestrator_path(
    flow_module_qa: types.ModuleType,
) -> None:
    """Calling the registered 'pass' tool POSTs to /api/v1/flow/qa/pass."""
    captured: list[tuple[str, Any]] = []

    def _client_factory(*_a: object, **_kw: object) -> MagicMock:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        def _post(url: str, **kwargs: object) -> MagicMock:
            captured.append((url, kwargs.get("json", {})))
            resp = MagicMock()
            resp.json.return_value = {
                "status": "awaiting_documentation",
                "error": None,
            }
            return resp

        client.post.side_effect = _post
        return client

    with patch("httpx.Client", side_effect=_client_factory):
        result = flow_module_qa.pass_review("task-id-123", notes="LGTM")

    assert result["status"] == "awaiting_documentation"
    orchestrator_calls = [(u, b) for u, b in captured if "test-orchestrator" in u]
    assert len(orchestrator_calls) == 1
    url, body = orchestrator_calls[0]
    assert url.endswith("/api/v1/flow/qa/pass"), (
        f"pass_review must POST to /qa/pass, got {url}"
    )
    assert body == {"task_id": "task-id-123", "notes": "LGTM"}
